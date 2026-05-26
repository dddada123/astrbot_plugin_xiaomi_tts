import aiohttp
import json
import re
from astrbot.api.all import *

@register("xiaomi_tts", "YourName", "1.0.0", "小米MiMo音色克隆：支持手动指令与LLM智能调用")
class XiaomiTTS(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

    # ==========================================
    # 核心底层逻辑：链接处理、切片与 API 调用
    # ==========================================
    
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
            self.context.logger.error(f"CDN链接转换失败: {e}")
            return original_url

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

    async def call_xiaomi_api(self, chunk_text: str, voice_url: str, prompt: str, api_key: str) -> str:
        """调用小米接口获取 Base64 音频"""
        url = "https://api.xiaomimimo.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": "mimo-v2.5-tts-voiceclone",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "audio_url", "audio_url": {"url": voice_url}}]},
                {"role": "assistant", "content": chunk_text}
            ]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status} - {await resp.text()}")
                res = await resp.json()
                try:
                    return res['choices'][0]['message']['audio']['data']
                except KeyError:
                    raise Exception("解析音频 Base64 失败，平台返回格式异常。")

    async def _generate_and_send_tts(self, event: AstrMessageEvent, text: str):
        """内部共用发音组件：处理验证并执行 TTS 流式发送"""
        api_key = self.config.get("api_key", "").strip()
        voice_prompt = self.config.get("voice_prompt", "保持参考音频的音色，用温柔的语气说话。").strip()
        
        if not api_key:
            yield event.plain_result("❌ 语音合成失败：未配置小米 API Key")
            return
            
        voice_url = self.get_final_audio_url()
        if not voice_url:
            yield event.plain_result("❌ 语音合成失败：未找到参考音频链接")
            return

        yield event.plain_result("🎙️ 小爱正在准备声音，请稍等...")
        
        for chunk in self.split_text(text):
            if not chunk: continue
            try:
                audio_b64 = await self.call_xiaomi_api(chunk, voice_url, voice_prompt, api_key)
                yield event.chain_result([Record(file=f"base64://{audio_b64}")])
            except Exception as e:
                self.context.logger.error(f"TTS 生成异常: {e}")
                yield event.plain_result(f"⚠️ 语音卡壳了: {str(e)[:50]}")
                break

    # ==========================================
    # 交互模式一：手动精准控制 (监听自定义前缀)
    # ==========================================
    
    @event_message_type(EventMessageType.ALL)
    async def manual_tts_trigger(self, event: AstrMessageEvent):
        """监听所有消息，匹配用户自定义的命令前缀"""
        trigger = self.config.get("command_trigger", "/tts").strip()
        msg = event.message_str.strip()

        # 如果消息是以触发词开头
        if msg.startswith(trigger):
            text_to_speak = msg[len(trigger):].strip()
            
            if not text_to_speak:
                yield event.plain_result("你想让我读什么呀？记得在指令后面加上文字哦~")
                return
            
            # 阻断 AstrBot 将该消息继续传给大模型进行普通对话
            event.stop_event()
            
            # 执行合成
            async for res in self._generate_and_send_tts(event, text_to_speak):
                yield res

    # ==========================================
    # 交互模式二：智能体自主决策 (LLM Tool 调用)
    # ==========================================
    
    @llm_tool(name="speak_tts")
    async def auto_speak_tts(self, event: AstrMessageEvent, text: str):
        """
        当用户在自然对话中要求发送语音、说话、或朗读某段文字时，调用此工具。
        Args:
            text (string): 需要转换成语音的文字内容
        """
        # 判断 WebUI 中是否开启了智能语音模式
        if not self.config.get("auto_voice_mode", False):
            yield event.plain_result("系统提示：当前未开启智能语音模式，无法发送语音。")
            return
        
        # 开启了智能模式，执行合成
        async for res in self._generate_and_send_tts(event, text):
            yield res