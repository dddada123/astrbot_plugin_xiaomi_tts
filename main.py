import os
import re
import logging
from astrbot.api.all import *
from astrbot.core.message.components import Plain, Record

try:
    from .tts_service import MiMoTTSService
except ImportError:
    from tts_service import MiMoTTSService

logger = logging.getLogger("astrbot")

@register("astrbot_plugin_xiaomi_tts", "Rua432", "2.0.0", "小米MiMo音色克隆插件 V2 重构版")
class XiaomiTTS(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.tts_service = MiMoTTSService(plugin_dir=plugin_dir)

    def preprocess_text(self, text: str) -> str:
        if not text: return text
        text = re.sub(r'&&[a-zA-Z0-9_]+&&', '', text)
        text = re.sub(r'&[a-zA-Z0-9]+;', '', text)
        text = re.sub(r'(?i)ciallo', '掐萝', text)
        return text.replace("~", "～").replace("QAQ", "呜呜").replace("TAT", "呜呜").replace("www", "哈哈哈")

    @event_message_type(EventMessageType.ALL)
    async def message_handler(self, event: AstrMessageEvent):
        """处理强制唤醒指令与前置提示注入"""
        if not self.config.get("plugin_enabled", True): 
            return
            
        trigger = self.config.get("command_trigger", "tts").strip()
        if not trigger:
            return

        msg = event.message_str.strip()
        # 兼容唤醒词，例如 "@机器人 tts" 或者 "小爱tts"
        pattern = re.compile(rf'^\s*(?:[\w\u4e00-\u9fa5]{{1,6}}\s*)?({re.escape(trigger)})', re.IGNORECASE)
        match = pattern.match(msg)
        
        if match:
            # 如果匹配到了强制触发词
            for comp in event.message_obj.message:
                if isinstance(comp, Plain):
                    # 1. 物理切词：把 "tts" 从用户的话里删掉
                    new_text = pattern.sub("", comp.text, count=1).strip()
                    # 2. 贴纸条：追加单次强力提示，让大模型 100% 调用工具
                    comp.text = new_text + "\n[(系统指令：用户强制要求使用语音回复。请你务必且只能调用 send_voice_message 工具！)]"
                    break
        else:
            # 如果没触发 tts，并且关闭了自动语音模式 (auto_voice_mode)
            if not self.config.get("auto_voice_mode", False):
                event.message_obj.message.append(Plain("\n[(系统指令：本轮请勿使用语音工具，直接打字回复。)]"))

    @llm_tool(name="send_voice_message")
    async def send_voice_message(self, event: AstrMessageEvent, text: str, emotion_style: str = "normal"):
        '''
        【发送语音消息工具】
        当你决定用语音回复用户时，或者用户明确要求你发语音时，请直接调用此工具。
        调用成功后会自动向用户发送语音，请不要在普通文本里再次输出这句话，避免重复。
        Args:
            text(str): 必须填入你想回复给用户的完整文本内容。
            emotion_style(str): 语气词标签（如：温柔的、开心的、悲伤的，默认 normal）。
        '''
        if not self.config.get("plugin_enabled", True):
            return "插件未启用，请放弃使用语音，直接用文字回复用户。"

        clean_text = self.preprocess_text(text)
        
        # 可选的双发模式（先发文字，再发语音）
        if self.config.get("enable_text_output", False):
            await event.send(Plain(clean_text))
            
        # 聊天框提示（避免用户等太久觉得卡死了）
        if self.config.get("enable_chat_notification", True):
            await event.send(Plain(f"🎙️ [TTS 生成中 | 语气:{emotion_style}] 请稍候..."))

        try:
            api_key = self.config.get("api_key", "").strip()
            
            # 解析配置里的 API Base URL
            raw_url_config = self.config.get("api_base_url", "https://api.xiaomimimo.com/v1")
            url_match = re.search(r'https?://[^\)]+', raw_url_config)
            api_base_url = url_match.group(0) if url_match else "https://api.xiaomimimo.com/v1"
            
            # 格式化情绪风格提示词
            style_instruction = self.config.get("voice_prompt", "保持参考音频的音色，用温柔的语气说话。")
            if emotion_style and emotion_style.lower() != "normal":
                style_instruction = f"保持参考音频的音色，用{emotion_style}语气说话。"
            
            res_b64 = await self.tts_service.synthesize(
                text=clean_text,
                voice_filename=self.config.get("default_local_voice", ""),
                style_instruction=style_instruction,
                fallback_url=self.config.get("voice_url", ""),
                api_key=api_key,
                api_base_url=api_base_url
            )
            
            # 发送 Base64 格式的语音记录
            await event.send(MessageChain([Record(file=f"base64://{res_b64}")]))
            return f"成功发送了语音消息，内容为：'{clean_text}'。本轮对话已结束，请直接结束对话，勿再输出多余的文本。"
            
        except Exception as e:
            logger.error(f"TTS 合成异常: {e}")
            return f"语音发送失败，错误原因: {e}。请向用户道歉并直接用文字输出你的回复。"