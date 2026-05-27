import os
import re
import logging
from astrbot.api.all import *
from astrbot.core.message.components import Plain

try:
    from .tts_service import MiMoTTSService
except ImportError:
    from tts_service import MiMoTTSService

logger = logging.getLogger("astrbot")

@register("xiaomi_tts", "Rua432", "1.1Beta", "小米MiMo音色克隆")
class XiaomiTTS(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.forced_sessions = set()
        self.session_styles = {}
        
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.tts_service = MiMoTTSService(
            api_key=self.config.get("api_key", ""),
            plugin_dir=plugin_dir
        )

    def preprocess_text(self, text: str) -> str:
        if not text: return text
        text = re.sub(r'(?i)ciallo', '掐萝', text)
        return text.replace("~", "～").replace("QAQ", "呜呜").replace("TAT", "呜呜").replace("www", "哈哈哈")

    async def _process_and_send(self, event: AstrMessageEvent, text: str):
        session_id = str(event.session_id)
        default_prompt = self.config.get("voice_prompt", "保持参考音频的音色，用温柔的语气说话。").strip()
        current_style = self.session_styles.get(session_id, default_prompt)
        
        style_display = current_style
        if '用' in current_style and '语气' in current_style:
            try:
                style_display = current_style.split('用')[-1].split('语气')[0]
            except:
                pass
        
        if self.config.get("enable_chat_notification", True):
            yield event.plain_result(f"🎙️ [当前语气:{style_display}] 正在生成完整语音...")

        try:
            cleaned_text = self.preprocess_text(text)
            res_b64 = await self.tts_service.synthesize(
                text=cleaned_text,
                voice_filename=self.config.get("default_local_voice", ""),
                style_instruction=current_style,
                fallback_url=self.config.get("voice_url", "")
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
        # === 新增：情绪降级，移除过于激昂的副词前缀，防止TTS音调变质 ===
        style_tag = re.sub(r'^(超级|极度|无比|万分)', '', style_tag)
        # === 新增结束 ===

        session_id = str(event.session_id)
        self.session_styles[session_id] = f"保持参考音频的音色，用{style_tag}语气说话。"
        
        if self.config.get("allow_prefix_text", False):
            return f"系统指令：语气已锁定为 {style_tag}。请继续调用 speak_tts 发送语音。"
        else:
            return f"系统指令：语气已锁定为 {style_tag}。现在请【立即】且【直接】调用 speak_tts 工具！绝对禁止在聊天框输出‘好嘞’、‘锁定完毕’等任何过渡废话！"

    @event_message_type(EventMessageType.ALL)
    async def message_handler(self, event: AstrMessageEvent):
        if not self.config.get("plugin_enabled", True): return
        
        trigger = self.config.get("command_trigger", "小爱tts").strip()
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
        # 【核心修正】：让模型把所有废话全塞进语音里
        # ==========================================
        allow_prefix = self.config.get("allow_prefix_text", False)
        
        # 读取字数限制，默认 50
        max_len = self.config.get("max_tts_text_length", 50)
        
        # 增加极其明确的 text 参数使用警告，并包含字数限制
        base_rule = f"\n[语音绝对规则：决定发语音时，必须先调用 set_voice_style，再调用 speak_tts。注意：你所有的回复内容（包括前置感叹词、正文以及诸如“晚安”、“再见”的结束语）必须【全部、毫无保留地】塞进 speak_tts 的 text 参数中！并且语音文本必须严格控制在 {max_len} 字以内，请极度精简你的表达！]"
        
        if not allow_prefix:
            final_prompt = base_rule + " [最高约束：调用完 speak_tts 后，你的任务就彻底结束了，必须立刻静默，绝对禁止再输出任何纯文字！]"
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
        if not self.config.get("plugin_enabled", True):
            return "管理员已关闭插件功能。"

        session_id = str(event.session_id)
        auto_mode = self.config.get("auto_voice_mode", False)
        is_forced = session_id in self.forced_sessions
        
        if is_forced:
            self.forced_sessions.discard(session_id)
        
        if not auto_mode and not is_forced:
            return "系统拦截：未开启智能语音模式，请使用纯文字回答用户。"

        # ===== 兜底截断：万一模型仍超长，强行保护 =====
        max_len = self.config.get("max_tts_text_length", 50)
        if len(text) > max_len:
            logger.info(f"TTS 文本过长({len(text)}字符)，自动截断至 {max_len} 字符")
            text = text[:max_len]
        # ===== 兜底结束 =====
        
        async for res in self._process_and_send(event, text):
            await event.send(res)
            
        # ==========================================
        # 【拦截后置总结文字的核心返回词】
        # ==========================================
        event.stop_event()