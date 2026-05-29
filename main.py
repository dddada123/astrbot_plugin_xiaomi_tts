import os
import re
import random
import logging
from astrbot.api.all import *
from astrbot.core.message.components import Plain

try:
    import yaml
except ImportError:
    yaml = None

try:
    from .tts_service import MiMoTTSService
except ImportError:
    from tts_service import MiMoTTSService

logger = logging.getLogger("astrbot")

@register("astrbot_plugin_xiaomi_tts", "Rua432", "1.7.0", "经典双轨+防缓存污染最终版")
class XiaomiTTS(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.session_states = {} 
        
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.tts_service = MiMoTTSService(plugin_dir=plugin_dir)

    def get_dynamic_cfg(self, key, default):
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
        text = re.sub(r'&&[a-zA-Z0-9_]+&&', '', text)
        text = re.sub(r'&[a-zA-Z0-9]+;', '', text)
        text = re.sub(r'(?i)ciallo', '掐萝', text)
        return text.replace("~", "～").replace("QAQ", "呜呜").replace("TAT", "呜呜").replace("www", "哈哈哈")

    @event_message_type(EventMessageType.ALL)
    async def message_handler(self, event: AstrMessageEvent):
        if not self.get_dynamic_cfg("plugin_enabled", True): return
        
        trigger = self.get_dynamic_cfg("command_trigger", "tts").strip()
        msg = event.message_str.strip()
        session_id = str(event.session_id)
        
        self.session_states[session_id] = "DISABLED"
        
        pattern = re.compile(rf'^\s*(?:[\w\u4e00-\u9fa5]{{1,6}}\s*)?({re.escape(trigger)})', re.IGNORECASE)
        match = pattern.match(msg)
        
        if match:
            logger.info("🎙️ [TTS路由] 语音匹配✅ -> 注入强制指令并执行物理切词")
            self.session_states[session_id] = "FORCED"
            
            for comp in event.message_obj.message:
                if isinstance(comp, Plain):
                    new_text = pattern.sub("", comp.text, count=1).strip()
                    if new_text != comp.text:
                        comp.text = new_text
                        break
                    
            prompt = "\n\n[最高指令：用户已下达语音指令。本轮你【必须】调用 set_voice_style 给100分，随后立即调用 speak_tts 发送语音。绝对禁止输出纯文字！]"
            event.message_obj.message.append(Plain(prompt))
            return

        # 🚨 【核心修复】：目标检测，防止污染群聊消息缓存
        # 检查是否为群聊，以及机器人是否被明确 @ 
        is_group = getattr(event.message_obj, 'group_id', None) is not None
        is_at = getattr(event, 'is_at_or_wake_word', False) or getattr(event.message_obj, 'is_at', False)
        
        # 如果是群聊里的普通闲聊（没被@），直接原样放行！不注入任何指令，保持缓存干干净净！
        if is_group and not is_at:
            return

        # 只有在私聊，或者群聊被 @ 时，才去注入系统指令
        if self.get_dynamic_cfg("auto_voice_mode", False):
            if random.random() < 0.05:
                logger.info("🎲 [TTS路由] 彩蛋匹配✅")
                self.session_states[session_id] = "FORCED_EASTER_EGG"
                prompt = "\n\n[系统指令：本轮触发语音彩蛋！你【必须】调用 set_voice_style 给100分，并调用 speak_tts 发送语音。禁止输出纯文本！]"
                event.message_obj.message.append(Plain(prompt))
            else:
                self.session_states[session_id] = "SMART"
                prompt = "\n\n[系统指令：本轮为智能语音模式。请自评语音渴望度(0-100)，先调用 set_voice_style 打分。若 >80 分，继续调用 speak_tts；若 <80 分，直接打字回复！]"
                event.message_obj.message.append(Plain(prompt))
        else:
            prompt = "\n\n[系统指令：本轮为纯文本聊天，严禁调用任何语音工具！直接输出文字。]"
            event.message_obj.message.append(Plain(prompt))

    @llm_tool(name="set_voice_style")
    async def set_voice_style(self, event: AstrMessageEvent, intent_score: int = 100, style_tag: str = "无"):
        '''
        【语音前置判定工具】
        注意：仅当系统提示中明确出现“[最高指令]”或“[系统指令]”要求你发语音时，才允许调用此工具！普通的纯文字聊天中，【绝对禁止】调用此工具！
        Args:
            intent_score(int): 语音渴望度(0-100)。如系统指令要求强制发音，请直接给100分。
            style_tag(str): 语气词标签（如：温柔的、害羞的、调皮的）。
        '''
        session_id = str(event.session_id)
        state = self.session_states.get(session_id, "DISABLED")
        
        if not isinstance(intent_score, int):
            intent_score = 50
            
        self.session_states[f"style_{session_id}"] = style_tag

        if state in ["FORCED", "FORCED_EASTER_EGG"]:
            logger.info(f"🧠 [TTS判定] 权限: {state} -> 锁定语气: {style_tag}")
            return "打分通过！锁定语气。请立即调用 speak_tts 发送语音，禁止输出普通文字！"
            
        elif state == "SMART":
            if intent_score >= 80:
                logger.info(f"🧠 [TTS判定] 智能打分: {intent_score} >= 80 -> 判定通过！语气: {style_tag}")
                return "打分通过！锁定语气。请立即调用 speak_tts 发送语音，禁止输出普通文字！"
            else:
                logger.info(f"🧠 [TTS判定] 智能打分: {intent_score} < 80 -> 分数不足，退回纯文本。")
                return "分数不足。请放弃调用 speak_tts，直接使用普通文本回复用户。"
                
        else:
            return "未开启语音模式，请放弃调用 speak_tts，使用普通文本回复。"

    @llm_tool(name="speak_tts")
    async def speak_tts(self, event: AstrMessageEvent, text: str):
        '''
        【语音发送工具】
        注意：必须在 set_voice_style 判定通过后才能调用！普通纯文字聊天【绝对禁止】调用此工具！
        Args:
            text(str): 必须填入你想回复给用户的完整文本内容。
        '''
        session_id = str(event.session_id)
        state = self.session_states.get(session_id, "DISABLED")
        style_tag = self.session_states.get(f"style_{session_id}", "无")
        
        clean_text = self.preprocess_text(text)
        
        if state == "DISABLED":
            yield event.plain_result(clean_text)
            return

        if self.get_dynamic_cfg("enable_text_output", False):
            yield event.plain_result(clean_text)

        style_tag = re.sub(r'^(超级|极度|无比|万分)', '', style_tag)
        current_style = f"保持参考音频的音色，用{style_tag}语气说话。"
            
        if self.get_dynamic_cfg("enable_chat_notification", True):
            yield event.plain_result(f"🎙️ [当前语气:{style_tag}] 正在生成完整语音...")

        try:
            api_key = self.get_dynamic_cfg("api_key", "").strip()
            raw_url_config = self.get_dynamic_cfg("api_base_url", "https://api.xiaomimimo.com/v1")
            url_match = re.search(r'https?://[^\)]+', raw_url_config)
            api_base_url = url_match.group(0) if url_match else "https://api.xiaomimimo.com/v1"
            
            res_b64 = await self.tts_service.synthesize(
                text=clean_text,
                voice_filename=self.get_dynamic_cfg("default_local_voice", ""),
                style_instruction=current_style,
                fallback_url=self.get_dynamic_cfg("voice_url", ""),
                api_key=api_key,
                api_base_url=api_base_url
            )
            yield event.chain_result([Record(file=f"base64://{res_b64}")])
            
        except Exception as e:
            logger.error(f"TTS 合成异常: {e}")
            yield event.plain_result(f"⚠️ 语音卡壳了: {e}")
            
        return
