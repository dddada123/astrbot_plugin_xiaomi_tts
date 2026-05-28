import os
import re
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

@register("astrbot_plugin_xiaomi_tts", "Rua432", "1.2.3Beta", "小米MiMo音色克隆(暴力直读配置版)")
class XiaomiTTS(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.forced_sessions = set()
        self.session_styles = {}
        
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.tts_service = MiMoTTSService(plugin_dir=plugin_dir)

    def get_dynamic_cfg(self, key, default):
        """暴力越过框架内存，直接去硬盘读 WebUI 的最新配置，解决空字典和无法热重载问题"""
        if yaml:
            try:
                # AstrBot 的配置文件固定在这个路径
                config_path = os.path.join(os.getcwd(), "data", "config.yaml")
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                        if data:
                            # 兼容新老名字的配置字典
                            plugin_cfg = data.get("astrbot_plugin_xiaomi_tts", data.get("xiaomi_tts", {}))
                            if key in plugin_cfg:
                                return plugin_cfg[key]
            except Exception:
                pass
        # 如果读硬盘失败，才退回使用框架给的配置
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
            
            # 实时读取 API 和 集群配置
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
    async def set_voice_style(self, event: AstrMessageEvent, style_tag: str):
        '''
        语音情绪总监。当你准备使用语音（speak_tts）回复时，请根据你即将输出的【回复内容本身的情感】，先调用此工具设定你的语气。
        Args:
            style_tag(str): 简短的语气词，如：撒娇的、开心的、傲娇的、委屈的、温柔的
        '''
        style_tag = re.sub(r'^(超级|极度|无比|万分)', '', style_tag)
        session_id = str(event.session_id)
        self.session_styles[session_id] = f"保持参考音频的音色，用{style_tag}语气说话。"
        
        if self.get_dynamic_cfg("allow_prefix_text", False):
            return f"系统指令：语气已锁定为 {style_tag}。请继续调用 speak_tts 发送语音。"
        else:
            return f"系统指令：语气已锁定为 {style_tag}。现在请【立即】且【直接】调用 speak_tts 工具！绝对禁止在聊天框输出‘好嘞’、‘锁定完毕’等任何过渡废话！"

    @event_message_type(EventMessageType.ALL)
    async def message_handler(self, event: AstrMessageEvent):
        if not self.get_dynamic_cfg("plugin_enabled", True): return
        
        trigger = self.get_dynamic_cfg("command_trigger", "小爱tts").strip()
        msg = event.message_str.strip()
        
        if msg.startswith(trigger):
            content = msg[len(trigger):].strip()
            if not content:
                yield event.plain_result("你想听什么？记得加上内容哦~")
                event.stop_event()
                return
            
            session_id = str(event.session_id)
            self.forced_sessions.add(session_id)
            
            for comp in event.message_obj.message:
                if isinstance(comp, Plain) and trigger in comp.text:
                    comp.text = comp.text.replace(trigger, "").strip()
            
            magic_prompt = "\n\n[系统指令：强制语音控制已开启！你必须调用 speak_tts 发送语音！]"
            event.message_obj.message.append(Plain(magic_prompt))

        # ==========================================
        # 核心防话痨拦截网（彻底补上前置文本的漏洞）
        # ==========================================
        allow_prefix = self.get_dynamic_cfg("allow_prefix_text", False)
        max_len = int(self.get_dynamic_cfg("max_tts_text_length", 50))
        
        base_rule = f"\n[语音绝对规则：决定发语音时，必须先调用 set_voice_style，再调用 speak_tts。注意：你所有的回复内容必须【全部、毫无保留地】塞进 speak_tts 的 text 参数中！并且语音文本必须严格控制在 {max_len} 字以内，极度精简！]"
        
        if not allow_prefix:
            # 物理级禁言令，不留任何发文字的漏洞
            final_prompt = base_rule + " [最高物理约束：本轮对话你【只能】通过工具发声！绝对禁止在聊天框输出任何纯文字（不准有前置台词、不准有动作描写、不准有后置解释）！想说的话必须全放进工具参数里，调用完毕立刻静默！]"
        else:
            final_prompt = base_rule + " [交互提示：在调用工具前可以发一句简短前置文字，但在 speak_tts 执行完后绝对禁止发任何后置文字！]"
            
        event.message_obj.message.append(Plain(final_prompt))

    @llm_tool(name="speak_tts")
    async def auto_speak_tts(self, event: AstrMessageEvent, text: str):
        '''
        语音合成发射器。
        Args:
            text(str): 你思考后决定回复给用户的完整文本（必须控制在限定字数内）
        '''
        if not self.get_dynamic_cfg("plugin_enabled", True):
            return "管理员已关闭插件功能。"

        session_id = str(event.session_id)
        auto_mode = self.get_dynamic_cfg("auto_voice_mode", False)
        is_forced = session_id in self.forced_sessions
        
        if is_forced:
            self.forced_sessions.discard(session_id)
        
        if not auto_mode and not is_forced:
            return "系统拦截：未开启智能语音模式，请使用纯文字回答用户。"

        max_len = int(self.get_dynamic_cfg("max_tts_text_length", 50))
        if len(text) > max_len:
            logger.info(f"TTS 文本过长({len(text)}字符)，自动截断至 {max_len} 字符")
            text = text[:max_len]
        
        async for res in self._process_and_send(event, text):
            await event.send(res)
            
        event.stop_event()
        return "语音已成功发送，对话完结。"