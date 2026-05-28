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

@register("astrbot_plugin_xiaomi_tts", "Rua432", "1.5.1Beta", "小米MiMo音色克隆(后台强制打分监控版)")
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
    async def set_voice_style(self, event: AstrMessageEvent, style_tag: str, intent_score: int = 0):
        '''
        意图打分与情绪总监。本轮对话你【必须】优先调用此工具来上报你的打分！
        Args:
            intent_score(int): 必须如实填入你对当前语境打出的“语音渴望度”分数(0-100)
            style_tag(str): 语气词(如: 撒娇的)。如果分数<60决定打字，请填"无"
        '''
        session_id = str(event.session_id)
        
        # 💡 核心可视化：在这里拦截大模型的打分，输出到你自己的后台日志！
        if intent_score < 80:
            logger.info(f"🧠 [TTS意图打分] 测评分数: {intent_score}分 -> 判定为不需要语音，大模型将被放行去纯打字。")
            return f"系统指令：打分 {intent_score} 已后台记录。由于低于80分，本轮【绝对禁止】调用 speak_tts，请直接在聊天框输出纯文字回复用户！"
        else:
            logger.info(f"🧠 [TTS意图打分] 测评分数: {intent_score}分 -> 判定通过！锁定语气: {style_tag}，准备发射语音！")
            style_tag = re.sub(r'^(超级|极度|无比|万分)', '', style_tag)
            self.session_styles[session_id] = f"保持参考音频的音色，用{style_tag}语气说话。"
            
            if self.get_dynamic_cfg("allow_prefix_text", False):
                return f"系统指令：打分 {intent_score} 已记录。语气已锁定为 {style_tag}。请继续调用 speak_tts 发送语音。"
            else:
                return f"系统指令：打分 {intent_score} 已记录。语气已锁定为 {style_tag}。现在请立即调用 speak_tts 工具！绝对禁止在聊天框输出任何废话！"

    @event_message_type(EventMessageType.ALL)
    async def message_handler(self, event: AstrMessageEvent):
        """处理用户的消息前缀。维持零污染设计"""
        if not self.get_dynamic_cfg("plugin_enabled", True): return
        
        trigger = self.get_dynamic_cfg("command_trigger", "小爱tts").strip()
        msg = event.message_str.strip()
        session_id = str(event.session_id)
        
        max_len = int(self.get_dynamic_cfg("max_tts_text_length", 50))
        allow_prefix = self.get_dynamic_cfg("allow_prefix_text", False)
        
        # 1. 手动强制模式
        if msg.startswith(trigger):
            self.forced_sessions.add(session_id)
            for comp in event.message_obj.message:
                if isinstance(comp, Plain) and trigger in comp.text:
                    comp.text = comp.text.replace(trigger, "").strip()
            
            rule = f"\n\n[最高指令：用户强制要求发语音！本轮你【必须】调用 set_voice_style 给出99分，然后调用 speak_tts。内容限 {max_len} 字以内！"
            rule += "可加前置文字但禁止后置文字！]" if allow_prefix else "绝对禁止在工具外打字！]"
            event.message_obj.message.append(Plain(rule))
            return

        # 2. 混合智能调度系统 (5% 彩蛋 + 强制意图上报)
        auto_mode = self.get_dynamic_cfg("auto_voice_mode", False)
        if auto_mode:
            lucky_drop = (random.random() < 0.05)
            
            if lucky_drop:
                logger.info(f"🎲 [TTS隐藏掉落] 抽中5%彩蛋概率！已下达强制发音指令。")
                rule = f"\n\n[隐藏指令：本轮请给群友个小惊喜！【必须】调用 set_voice_style 给出99分，并调用 speak_tts 发声！内容限 {max_len} 字！"
                rule += "可加前置文字但禁止后置文字！]" if allow_prefix else "绝对禁止在工具外打字！]"
                event.message_obj.message.append(Plain(rule))
            else:
                # 🎯 强制大模型每次都“交卷”打分，这样你后台就一定能看到分数
                cognitive_gate_prompt = (
                    f"\n\n[🧠 智能语音决策系统]\n"
                    f"1. 强制上报：无论你决定发语音还是纯打字，本轮【必须】作为第一步调用 set_voice_style 工具上报你的意图打分(0-100)！\n"
                    f"2. 打分标准：日常闲聊低于40分；用户明确要求听声音、早晚安仪式、强烈傲娇撒娇互动高于80分。\n"
                    f"3. 语音约束：如果你决定发语音(打分>=80)，在调完 set_voice_style 后，必须紧接调用 speak_tts（限 {max_len} 字，且绝对禁止在工具外打字废话）。"
                )
                event.message_obj.message.append(Plain(cognitive_gate_prompt))

    @llm_tool(name="speak_tts")
    async def auto_speak_tts(self, event: AstrMessageEvent, text: str):
        '''
        发送语音给用户。必须在上报打分>=80分后才能调用！
        Args:
            text(str): 语音合成的完整文本（尽量精简）
        '''
        if not self.get_dynamic_cfg("plugin_enabled", True):
            return "管理员已关闭插件功能。"

        session_id = str(event.session_id)
        auto_mode = self.get_dynamic_cfg("auto_voice_mode", False)
        is_forced = session_id in self.forced_sessions
        
        if is_forced:
            self.forced_sessions.discard(session_id)
        else:
            if not auto_mode:
                return "系统拦截：未开启智能语音模式，请使用纯文字回答用户。"

        max_len = int(self.get_dynamic_cfg("max_tts_text_length", 50))
        if len(text) > max_len:
            logger.info(f"TTS 文本过长({len(text)}字符)，自动截断至 {max_len} 字符")
            text = text[:max_len]
        
        async for res in self._process_and_send(event, text):
            await event.send(res)
            
        event.stop_event()
        return "语音已成功发送，对话完结。"
