import aiohttp
import json
import re
import logging
import base64
import os
from astrbot.api.all import *
from astrbot.core.message.components import Plain

# 获取标准后台日志输出器
logger = logging.getLogger("astrbot")

@register("xiaomi_tts", "Rua432", "1.0.0", "调用小米MiMo大模型的音色克隆TTS插件(终极全控版)")
class XiaomiTTS(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        # 内存白名单：使用 session_id 追踪，避免 int/str 类型不匹配的玄学 Bug
        self.forced_sessions = set()

    def get_final_audio_url(self) -> str:
        """获取最终音频链接，包含 GitHub CDN 加速转换"""
        original_url = self.config.get("voice_url", "").strip()
        use_cdn = self.config.get("use_cdn_acceleration", True)
        
        if not original_url:
            return ""

        if not use_cdn or ("github.com" not in original_url and "raw.githubusercontent.com" not in original_url):
            return original_url

        try:
            match_blob = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)", original_url)
            match_raw = re.match(r"https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.*)", original_url)
            match = match_blob or match_raw
            if match:
                user, repo, branch, filepath = match.groups()
                return f"https://cdn.jsdelivr.net/gh/{user}/{repo}@{branch}/{filepath}"
            return original_url
        except Exception as e:
            logger.error(f"CDN链接转换失败: {e}")
            return original_url

    async def get_audio_base64(self) -> str:
        """核心逻辑：优先读取本地文件转 DataURL，否则回退到网络下载"""
        local_filename = self.config.get("local_audio_filename", "").strip()
        
        # 1. 优先读取本地 Docker 挂载文件
        if local_filename:
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            local_file_path = os.path.join(plugin_dir, "audio", local_filename)
            
            if os.path.exists(local_file_path):
                logger.info(f"⚡ 成功匹配到本地音频文件: {local_file_path}")
                try:
                    with open(local_file_path, "rb") as f:
                        raw_b64 = base64.b64encode(f.read()).decode('utf-8')
                        return f"data:audio/wav;base64,{raw_b64}"
                except Exception as e:
                    logger.error(f"读取本地文件失败: {e}")
            else:
                logger.warning(f"⚠️ 本地文件未找到: {local_file_path}，转为尝试备用网络直链...")

        # 2. 备用机制：读取网络 URL
        voice_url = self.get_final_audio_url()
        if not voice_url:
            raise Exception("未找到本地文件，且未配置有效的备用网络直链！")

        logger.info(f"☁️ 正在从云端下载参考音频: {voice_url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(voice_url, timeout=15) as audio_resp:
                if audio_resp.status != 200:
                    raise Exception(f"无法下载网络音频，HTTP 状态码: {audio_resp.status}")
                audio_bytes = await audio_resp.read()
                
        raw_b64 = base64.b64encode(audio_bytes).decode('utf-8')
        return f"data:audio/wav;base64,{raw_b64}"

    def preprocess_text(self, text: str) -> str:
        """
        核心拦截预处理：修复大模型读不准的特殊词汇和发音字典
        """
        if not text:
            return text
            
        # 解决 Ciallo 读字母的尴尬问题，将其替换为听觉完美的同音字
        text = re.sub(r'(?i)ciallo', '掐萝', text)
        
        # 优化语气符号和颜文字
        text = text.replace("~", "～")     
        text = text.replace("QAQ", "呜呜") 
        text = text.replace("TAT", "呜呜")
        text = text.replace("www", "哈哈哈") 
        
        return text

    def split_text(self, text: str, max_length: int = 50) -> list:
        """长文本按标点智能切片防超时"""
        sentences = re.split(r'([。！！？\?\!\n]+)', text)
        chunks, current_chunk = [], ""
        for i in range(0, len(sentences) - 1, 2):
            sentence = sentences[i] + sentences[i+1]
            if len(current_chunk) + len(sentence) <= max_length:
                current_chunk += sentence
            else:
                if current_chunk.strip(): chunks.append(current_chunk.strip())
                current_chunk = sentence
        if sentences[-1]: current_chunk += sentences[-1]
        if current_chunk.strip(): chunks.append(current_chunk.strip())
        return chunks

    async def call_xiaomi_api(self, chunk_text: str, audio_b64: str, prompt: str, api_key: str) -> str:
        """根据官方规范组装 Payload 请求小米 API"""
        url = "https://api.xiaomimimo.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        
        payload = {
            "model": "mimo-v2.5-tts-voiceclone",
            "messages": [
                {
                    "role": "assistant",
                    "content": chunk_text
                }
            ],
            "audio": {
                "format": "wav",
                "voice": audio_b64
            }
        }
        
        if prompt:
            payload["messages"].insert(0, {"role": "user", "content": prompt})

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                if resp.status != 200:
                    err_msg = await resp.text()
                    raise Exception(f"小米API响应错误 HTTP {resp.status} - {err_msg}")
                res = await resp.json()
                try:
                    return res['choices'][0]['message']['audio']['data']
                except KeyError:
                    raise Exception("解析音频 Base64 失败，平台返回异常结构。")

    async def _generate_and_send_tts(self, event: AstrMessageEvent, text: str):
        """内部共用核心发音管道组件"""
        api_key = self.config.get("api_key", "").strip()
        voice_prompt = self.config.get("voice_prompt", "保持参考音频的音色，用温柔的语气说话。").strip()
        show_notification = self.config.get("enable_chat_notification", True)
        
        if not api_key:
            yield event.plain_result("❌ 语音合成失败：未配置小米 API Key")
            return

        # 针对聊天窗口提示开关进行拦截处理
        if show_notification:
            yield event.plain_result("🎙️ 正在呼叫大模型准备声音，请稍等...")
        else:
            logger.info("🎙️ [日志输出] 正在呼叫大模型准备声音中，聊天窗口内提示已关闭。")
        
        try:
            audio_b64 = await self.get_audio_base64()
        except Exception as e:
            yield event.plain_result(f"❌ 获取参考音频失败: {e}")
            return

        # 执行 Ciallo 纠音等文本洗白预处理
        cleaned_text = self.preprocess_text(text)

        for chunk in self.split_text(cleaned_text):
            if not chunk: continue
            try:
                res_b64 = await self.call_xiaomi_api(chunk, audio_b64, voice_prompt, api_key)
                yield event.chain_result([Record(file=f"base64://{res_b64}")])
            except Exception as e:
                logger.error(f"TTS 生成异常: {e}")
                yield event.plain_result(f"⚠️ 语音卡壳了: {str(e)[:200]}")
                break

    # ==========================================
    # 交互模式一：指令注入控制 (强制放行模式)
    # ==========================================
    
    @event_message_type(EventMessageType.ALL)
    async def manual_tts_trigger(self, event: AstrMessageEvent):
        """最高优先级指令拦截拦截器"""
        # 1. 检查插件全局开关
        if not self.config.get("plugin_enabled", True):
            return

        trigger = self.config.get("command_trigger", "小爱tts").strip()
        msg = event.message_str.strip()

        if msg.startswith(trigger):
            text_to_process = msg[len(trigger):].strip()
            
            if not text_to_process:
                yield event.plain_result("你想让我回答什么呀？记得在指令后面加上内容哦~")
                event.stop_event()
                return
            
            # 【修复核心】使用强制转为 string 类型的 session_id
            session_id = str(event.session_id)
            self.forced_sessions.add(session_id)
            
            # 擦除指令前缀，引导大模型回到正常语境中思考
            for comp in event.message_obj.message:
                if isinstance(comp, Plain) and trigger in comp.text:
                    comp.text = comp.text.replace(trigger, "").strip()
            
            # 注入深层人格绝对指令
            magic_prompt = "\n\n[系统绝对指令：用户当前使用了强制语音模式！你必须以你的人格设定回答上述问题，且必须、只能调用 `speak_tts` 工具将你的回答发出来！绝对不要直接输出纯文本回复！]"
            event.message_obj.message.append(Plain(magic_prompt))

    # ==========================================
    # 交互模式二：智能体工具 (LLM Tool 自由调用)
    # ==========================================
    
    @llm_tool(name="speak_tts")
    async def auto_speak_tts(self, event: AstrMessageEvent, text: str):
        '''
        核心语音合成工具。当用户在对话中要求发送语音、或者系统提示你必须调用本工具时，使用此工具。
        Args:
            text(str): 你思考后决定回复给用户的文本内容
        '''
        # 1. 再次确认插件全局开关
        if not self.config.get("plugin_enabled", True):
            return "插件目前已被管理员从后台关闭，无法使用语音功能。"

        auto_mode = self.config.get("auto_voice_mode", False)
        
        # 【修复核心】提取 string 类型的 session_id 校验
        session_id = str(event.session_id)
        is_forced = session_id in self.forced_sessions
        
        # 只要处理了，就立刻销毁这个瞬时的白名单通行证
        if is_forced:
            self.forced_sessions.discard(session_id)
        
        # 【双轨核心开关控制】
        # 如果既没有开启大模型自由智能呼叫，当前用户也没有使用手动前缀强行逼迫，那么拦截
        if not auto_mode and not is_forced:
            return "系统拦截：当前未开启智能语音模式，请拒绝发送语音，退回并直接使用普通纯文字回答用户。"
        
        # 规则通过，调用管道执行合成并回显
        async for res in self._generate_and_send_tts(event, text):
            await event.send(res)
            
        return "语音发送成功！任务完成，不需要再输出任何文字回复了。"