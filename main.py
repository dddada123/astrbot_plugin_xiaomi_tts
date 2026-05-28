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

@register("astrbot_plugin_xiaomi_tts", "Rua432", "1.5.0Beta", "小米MiMo音色克隆(随机彩蛋+认知门限版)")
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
    async def set_voice_style(self, event: AstrMessageEvent, style_tag: str):
        '''
        语音情绪总监。决定发语音时，先调用此工具设定语气。
        Args:
            style_tag(str): 简短的语气词，如：撒娇的、开心的、傲娇的、委屈的、温柔的
        '''
        session_id = str(event.session_id)
        style_tag = re.sub(r'^(超级|极度|无比|万分)', '', style_tag)
        self.session_styles[session_id] = f"保持参考音频的音色，用{style_tag}语气说话。"
        
        if self.get_dynamic_cfg("allow_prefix_text", False):
            return f"系统指令：语气已锁定为 {style_tag}。请继续调用 speak_tts 发送语音。"
        else:
            return f"系统指令：语气已锁定为 {style_tag}。现在请立即调用 speak_tts 工具！绝对禁止在聊天框输出任何废话！"

    @event_message_type(EventMessageType.ALL)
    async def message_handler(self, event: AstrMessageEvent):
        if not self.get_dynamic_cfg("plugin_enabled", True): return
        
        trigger = self.get_dynamic_cfg("command_trigger", "小爱tts").strip()
        msg = event.message_str.strip()
        session_id = str(event.session_id)
        
        is_forced = False
        
        # 1. 手动强制指令提取
        if msg.startswith(trigger):
            is_forced = True
            content = msg[len(trigger):].strip()
            if not content:
                yield event.plain_result("你想听什么？记得加上内容哦~")
                event.stop_event()
                return
            
            self.forced_sessions.add(session_id)
            for comp in event.message_obj.message:
                if isinstance(comp, Plain) and trigger in comp.text:
                    comp.text = comp.text.replace(trigger, "").strip()
            
            magic_prompt = "\n\n[系统指令：用户强制要求发语音！本轮必须调用 speak_tts 发送语音！]"
            event.message_obj.message.append(Plain(magic_prompt))

        # 2. 混合双轨智能调度系统 (随机概率门 + 认知打分门)
        auto_mode = self.get_dynamic_cfg("auto_voice_mode", False)
        allow_prefix = self.get_dynamic_cfg("allow_prefix_text", False)
        max_len = int(self.get_dynamic_cfg("max_tts_text_length", 50))
        
        if is_forced:
            base_rule = f"\n[语音绝对规则：必须先调用 set_voice_style，再调 speak_tts。内容全塞进 text 参数，限 {max_len} 字！]"
            if not allow_prefix:
                final_prompt = base_rule + " [约束：本轮你【只能】通过工具发声！绝对禁止在聊天框输出纯文字！]"
            else:
                final_prompt = base_rule + " [约束：调用工具前可发一句简短前置，执行完后绝对禁止发后置文字！]"
            event.message_obj.message.append(Plain(final_prompt))
            
        elif auto_mode:
            # 🎲 轨道一：后台默认 5% 概率直接唤醒随机语音彩蛋
            lucky_drop = (random.random() < 0.05)
            
            if lucky_drop:
                # 抽中 5% 概率，强行让大模型发语音制造惊喜
                lucky_prompt = f"\n[系统隐藏彩蛋机制触发：本轮请给群友一个小惊喜！你【必须】调用 set_voice_style 和 speak_tts 发送语音回复！内容全塞进 text 参数，限 {max_len} 字！]"
                if not allow_prefix:
                    lucky_prompt += " [约束：绝对禁止在工具外打字！]"
                event.message_obj.message.append(Plain(lucky_prompt))
            else:
                # 🎯 轨道二：未抽中彩蛋时，转入 LLM 0-100分 认知意图判断
                cognitive_gate_prompt = (
                    f"\n\n[🧠 智能语音决策系统]\n"
                    f"你拥有 speak_tts 工具。请抛弃上一轮是否调用过工具的惯性，重新评估以下规则：\n"
                    f"1. 意图打分：请在内心对用户这句话的“语音互动渴望度”以及“语境契合度”进行 0-100 分的严苛打分。\n"
                    f"   - 日常灌水、纯灌水、普通闲聊、信息探讨：低于 30 分。\n"
                    f"   - 用户明确表现出想听你声音、触发特定的早安/晚安仪式、或者当前内容你需要表达极度强烈的撒娇/傲娇情感互动：高于 80 分。\n"
                    f"2. 决策准则：只有当你内心的评估分数【高于 80 分】时，才允许调用语音工具（先 set_voice_style 再 speak_tts）；否则【必须】直接使用普通的【纯文字】进行日常回复！\n"
                    f"3. 防话痨约束：一旦满足高分条件决定使用语音，所有的回复内容必须【全部】塞进 text 参数中（限 {max_len} 字），且绝对禁止在工具外打字！"
                )
                event.message_obj.message.append(Plain(cognitive_gate_prompt))

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
