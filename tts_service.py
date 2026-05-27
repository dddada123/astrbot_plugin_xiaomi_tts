import os
import base64
import logging
import aiohttp
from typing import Optional

logger = logging.getLogger("astrbot")

class MiMoTTSService:
    """MiMo TTS 底层通信与音频缓存服务 (动态配置版)"""
    
    def __init__(self, plugin_dir: str):
        self.plugin_dir = plugin_dir
        # 内存缓存：存放所有本地音频的 DataURL (音频预加载依然保留以保证性能)
        self.voices_cache = {}
        self._load_local_voices()

    def _load_local_voices(self):
        """启动时一次性预加载 audio 目录下的所有音频，避免高并发读盘"""
        audio_dir = os.path.join(self.plugin_dir, "audio")
        if not os.path.exists(audio_dir):
            os.makedirs(audio_dir, exist_ok=True)
            logger.warning(f"音频目录不存在，已自动创建: {audio_dir}")
            return
            
        for filename in os.listdir(audio_dir):
            if filename.lower().endswith(('.wav', '.mp3')):
                file_path = os.path.join(audio_dir, filename)
                try:
                    with open(file_path, "rb") as f:
                        raw_b64 = base64.b64encode(f.read()).decode('utf-8')
                        mime_type = "audio/wav" if filename.lower().endswith('.wav') else "audio/mpeg"
                        self.voices_cache[filename] = f"data:{mime_type};base64,{raw_b64}"
                except Exception as e:
                    logger.error(f"预加载音频 {filename} 失败: {e}")
                    
        logger.info(f"✅ TTS 服务已就绪，内存中成功预加载了 {len(self.voices_cache)} 个本地音色。")

    async def get_fallback_network_voice(self, url: str) -> str:
        """备用网络下载通道"""
        if not url:
            raise Exception("本地未找到指定音色，且未配置备用网络链接。")
        logger.info(f"正在拉取备用网络音频: {url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    raise Exception(f"网络音频下载失败: HTTP {resp.status}")
                audio_bytes = await resp.read()
                return f"data:audio/wav;base64,{base64.b64encode(audio_bytes).decode('utf-8')}"

    # === 新增：每次合成时，实时接收 api_key 和 api_base_url ===
    async def synthesize(self, text: str, voice_filename: str, style_instruction: str, fallback_url: str, api_key: str, api_base_url: str) -> str:
        """核心合成请求方法，不再分片，长文本一次性请求"""
        if not api_key:
            raise Exception("未配置小米 API Key。")
            
        if not (api_key.startswith("sk-") or api_key.startswith("tp-")):
            logger.warning(f"⚠️ 警告: 你的小米 API Key 格式可能不正确（当前以 {api_key[:3]} 开头）。按量付费应为 sk-，Token Plan 应为 tp-。")

        voice_data = self.voices_cache.get(voice_filename)
        if not voice_data:
            voice_data = await self.get_fallback_network_voice(fallback_url)

        payload = {
            "model": "mimo-v2.5-tts-voiceclone",
            "messages": [{"role": "assistant", "content": text}],
            "audio": {
                "format": "wav",
                "voice": voice_data
            }
        }
        
        if style_instruction:
            payload["messages"].insert(0, {"role": "user", "content": style_instruction})

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{api_base_url}/chat/completions", json=payload, headers=headers, timeout=60) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    raise Exception(f"小米API报错 HTTP {resp.status}: {err_text}")
                res = await resp.json()
                try:
                    return res['choices'][0]['message']['audio']['data']
                except KeyError:
                    raise Exception("解析 Base64 失败，返回体结构异常。")