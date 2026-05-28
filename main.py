import os
import re
import random
import logging
from astrbot.api.all import *
from astrbot.core.message.components import Plain

# 引入 yaml 用于暴力直读本地配置
try:
    import yaml
except ImportError:
    yaml = None

try:
    from .tts_service import MiMoTTSService
except ImportError:
    from tts_service import MiMoTTSService

logger = logging.getLogger("astrbot")

@register("astrbot_plugin_xiaomi_tts", "Rua432", "1.5.2Beta", "小米MiMo音色克隆(极简Token零污染版)")
class XiaomiTTS(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.forced_sessions = set()
        self.session_styles = {}
        
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.tts_service = MiMoTTSService(plugin_dir=plugin_dir)

    def get_dynamic_cfg(self, key, default):
        """暴力越过框架内存，直接去硬盘读 WebUI 的最新配置"""
        if yaml:
            try:
                config_path = os.path.join(os.getcwd(), "data", "config.yaml")
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                        if data:
                            plugin_cfg = data.get("astrbot_plugin_xiaomi_tts", data.get("xiaomi_tts", {}))
                            if key in plugin_cfg:
                                return plugin_cfg[key]
            except Exception:
                pass
        return self.config.get(key, default)

    def preprocess_text(self, text: str) -> str:
        if not text: return text
        text = re.sub(r'(?i)ciallo', '掐萝', text)
        return text.replace("~", "～").replace("QAQ", "呜呜").replace("TAT", "呜呜").replace("www", "哈哈哈")

    async def _process_and_send(self, event: AstrMessageEvent, text: str):
        session_id = str(event.session_id)
        default_prompt = self.get_dynamic_cfg("voice_prompt", "保持参考音频的音色，用温柔的语气说话。").strip()
        current_style = self.session_styles.get(session_id, default_prompt)
        
        style_display = current_style
        if '用' in current_style and '语气' in current_style:
            try:
                style_display = current_style.split('用')[-1].split('语气')[0]
            except:
                pass
        
        if self.get_dynamic_cfg("enable_chat_notification", True):
            yield event.plain_result(f"🎙️ [当前语气:{style_display}] 正在生成完整语音...")

        try:
            cleaned_text = self.preprocess_text(text)
            
            api_key = self.get_dynamic_cfg("api_key", "").strip()
            raw_url_config = self.get_dynamic_cfg("api_base_url", "https://api.xiaomimimo.com/v1")
            url_match = re.search(r'https?://[^\)]+', raw_url_config)
            api_base_url = url_match.group(0) if url_match else "https://api.xiaomimimo.com/v1"
            
            res_b64 = await self.tts_service.synthesize(
                text=cleaned_text,
                voice_filename=self.get_dynamic_cfg("default_local_voice", ""),
                style_instruction=current_style,
                fallback_url=self.get_dynamic_cfg("voice_url", ""),
                api_key=api_key,
                api_base_url=api_base_url
            )
            yield event.chain_result([Record(file=f"base64://{res_b64}")])
            
            if session_id in self.session_styles:
                del self.session_styles[session_id]
        except Exception as e:
            logger.error(f"TTS 合成异常: {e}")
            yield event.plain_result(f"⚠️ 语音卡壳了: {e}")

    @llm_tool(name="set_voice_style")
    async def set_voice_style(self, event: AstrMessageEvent, intent_score: int, style_tag: str = "无"):
        '''
        【必须优先调用】意图打分与情绪设定。无论是否发语音，回复前必须先调此工具上报分数。
        Args:
            intent_score(int): 对语境“语音互动渴望度”打分(0-100)。闲聊<40，强烈互动/要求语音>80。
            style_tag(str): 语气词(如: 撒娇的)。若分数<80请填"无"。
        '''
        session_id = str(event.session_id)
        
        if intent_score < 80:
            logger.info(f"🧠 [TTS打分] {intent_score}分 -> 分数不足，放行纯文字回复。")
            return f"已记录。分数{intent_score}<80，禁止语音，请输出文字。"
        else:
            logger.info(f"🧠 [TTS打分] {intent_score}分 -> 判定通过！锁定语气: {style_tag}")
            style_tag = re.sub(r'^(超级|极度|无比|万分)', '', style_tag)
            self.session_styles[session_id] = f"保持参考音频的音色，用{style_tag}语气说话。"
            
            if self.get_dynamic_cfg("allow_prefix_text", False):
                return f"锁定{style_tag}。分数>=80，请调用 speak_tts 发音。"
            else:
                return f"锁定{style_tag}。分数>=80，请立即调用 speak_tts，禁发文字！"

    @event_message_type(EventMessageType.ALL)
    async def message_handler(self, event: AstrMessageEvent):
        """真正的零污染处理，坚决不向非强制消息里塞规则"""
        if not self.get_dynamic_cfg("plugin_enabled", True): return
        
        trigger = self.get_dynamic_cfg("command_trigger", "小爱tts").strip()
        msg = event.message_str.strip()
        session_id = str(event.session_id)
        
        # 1. 手动强制模式
        if msg.startswith(trigger):
            self.forced_sessions.add(session_id)
            for comp in event.message_obj.message:
                if isinstance(comp, Plain) and trigger in comp.text:
                    comp.text = comp.text.replace(trigger, "").strip()
            
            max_len = int(self.get_dynamic_cfg("max_tts_text_length", 50))
            allow_prefix = self.get_dynamic_cfg("allow_prefix_text", False)
            rule = f"\n\n[最高指令：强制发音！本轮必须调用 set_voice_style (给99分) 然后调用 speak_tts(限{max_len}字)。"
            rule += "可加前置文字！]" if allow_prefix else "禁发纯文字！]"
            event.message_obj.message.append(Plain(rule))
            return

        # 2. 5% 惊喜彩蛋 (仅在后台摇号，中奖了才加规则，不中奖【绝对不加】任何东西)
        auto_mode = self.get_dynamic_cfg("auto_voice_mode", False)
        if auto_mode:
            lucky_drop = (random.random() < 0.05)
            if lucky_drop:
                logger.info(f"🎲 [TTS彩蛋] 摇号中签！强制发音。")
                max_len = int(self.get_dynamic_cfg("max_tts_text_length", 50))
                allow_prefix = self.get_dynamic_cfg("allow_prefix_text", False)
                rule = f"\n\n[隐藏彩蛋：本轮必须调用 set_voice_style (给99分) 然后调用 speak_tts(限{max_len}字)。"
                rule += "可加前置文字！]" if allow_prefix else "禁发纯文字！]"
                event.message_obj.message.append(Plain(rule))
            # 未中签时，直接放行，没有任何隐式规则污染群聊记录！

    @llm_tool(name="speak_tts")
    async def auto_speak_tts(self, event: AstrMessageEvent, text: str):
        '''
        发送语音给用户。必须在上报打分>=80分后才能调用！
        Args:
            text(str): 语音合成的完整文本（尽量精简）
        '''
        if not self.get_dynamic_cfg("plugin_enabled", True):
            return "未启用插件。"

        session_id = str(event.session_id)
        auto_mode = self.get_dynamic_cfg("auto_voice_mode", False)
        is_forced = session_id in self.forced_sessions
        
        if is_forced:
            self.forced_sessions.discard(session_id)
        else:
            if not auto_mode:
                return "未开启智能模式，禁止语音。"

        max_len = int(self.get_dynamic_cfg("max_tts_text_length", 50))
        if len(text) > max_len:
            logger.info(f"TTS 文本过长({len(text)}字符)，自动截断至 {max_len} 字符")
            text = text[:max_len]
        
        async for res in self._process_and_send(event, text):
            await event.send(res)
            
        event.stop_event()
        return "发送成功。"
