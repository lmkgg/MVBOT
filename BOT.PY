# bot.py - ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ
import discord
from discord.ext import commands
import yt_dlp
import asyncio
import logging
import os
import random

# === ТОКЕН ===
TOKEN = ""
PREFIX = "!"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('discord')

# Проверка cookies
COOKIES_FILE = "cookies.txt"
if os.path.exists(COOKIES_FILE):
    logger.info(f"✅ Cookies найдены: {COOKIES_FILE}")
else:
    logger.warning("⚠️ Cookies не найдены!")

# Заголовки как у Android приложения YouTube (ОБХОДИТ БЛОКИРОВКИ)
USER_AGENTS = [
    'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
    'com.google.android.youtube/19.02.39 (Linux; U; Android 13; RU) gzip',
]

# КРИТИЧЕСКИ ВАЖНЫЕ настройки
ydl_opts = {
    'format': 'bestaudio[ext=m4a]/bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'ignoreerrors': True,
    'no_color': True,
    'socket_timeout': 15,
    'retries': 5,
    'fragment_retries': 5,
    'skip_unavailable_fragments': True,
    'geo_bypass': True,
    'geo_bypass_country': 'US',
    'headers': {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-us,en;q=0.5',
        'Sec-Fetch-Mode': 'navigate',
    },
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'ios', 'web'],
            'skip': ['hls', 'dash'],
        }
    },
}

# Если есть cookies - добавляем
if os.path.exists(COOKIES_FILE):
    ydl_opts['cookiefile'] = COOKIES_FILE

ffmpeg_opts = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
queues = {}

class MusicPlayer:
    def __init__(self):
        self.queue = []
    def add(self, url):
        self.queue.append(url)
    def clear(self):
        self.queue.clear()
    def next(self):
        return self.queue.pop(0) if self.queue else None

@bot.event
async def on_ready():
    logger.info(f'✅ Бот {bot.user} готов!')
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, 
        name=f"{PREFIX}play | YouTube"
    ))

def get_audio(url):
    """Получение аудио - выполняется синхронно в отдельном потоке"""
    try:
        # Пробуем с разными настройками
        for attempt in range(3):
            try:
                # Меняем User-Agent при каждой попытке
                ydl_opts['headers']['User-Agent'] = random.choice(USER_AGENTS)
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    
                    if not info:
                        continue
                    
                    # Извлекаем из плейлиста если нужно
                    if 'entries' in info:
                        entries = [e for e in info['entries'] if e]
                        if entries:
                            info = entries[0]
                        else:
                            continue
                    
                    # Получаем аудио URL
                    audio_url = info.get('url')
                    
                    # Если нет прямого URL - ищем в форматах
                    if not audio_url and 'formats' in info:
                        for fmt in info['formats']:
                            url_candidate = fmt.get('url')
                            if url_candidate:
                                # Проверяем что это аудио формат
                                acodec = fmt.get('acodec', '')
                                if acodec and acodec != 'none':
                                    audio_url = url_candidate
                                    break
                    
                    if audio_url:
                        return {
                            'url': audio_url,
                            'title': info.get('title', 'Без названия'),
                            'duration': info.get('duration', 0)
                        }
                        
            except Exception as e:
                if attempt < 2:
                    continue
                logger.error(f"Попытка {attempt+1}: {e}")
        
        return None
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return None

async def get_audio_async(url):
    """Асинхронная обертка"""
    try:
        return await asyncio.wait_for(
            bot.loop.run_in_executor(None, get_audio, url),
            timeout=20.0
        )
    except asyncio.TimeoutError:
        logger.error("Таймаут")
        return None

async def play_next(ctx):
    """Воспроизведение следующего трека"""
    guild_id = ctx.guild.id
    if guild_id not in queues:
        queues[guild_id] = MusicPlayer()
    
    player = queues[guild_id]
    voice = ctx.voice_client
    
    if not voice or not voice.is_connected():
        return await ctx.send("❌ Нет соединения")
    
    url = player.next()
    if not url:
        return await ctx.send("📭 Очередь пуста!")
    
    msg = await ctx.send("🔍 Поиск аудио...")
    
    audio = await get_audio_async(url)
    
    if not audio:
        await msg.edit(content="❌ Не удалось загрузить\n"
                       "• Проверьте ссылку\n"
                       "• Убедитесь что cookies.txt существует\n"
                       "• Возможно нужен VPN")
        if player.queue:
            return await play_next(ctx)
        return
    
    try:
        source = discord.FFmpegPCMAudio(audio['url'], **ffmpeg_opts)
        source = discord.PCMVolumeTransformer(source, 0.5)
        
        def after(error):
            if error:
                logger.error(f"Ошибка: {error}")
            asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        
        voice.play(source, after=after)
        
        dur = audio['duration']
        time_str = f" ({dur//60}:{dur%60:02d})" if dur else ""
        await msg.edit(content=f"🎵 **{audio['title']}**{time_str}")
        
    except Exception as e:
        await msg.edit(content=f"❌ Ошибка: {e}")
        if player.queue:
            await play_next(ctx)

@bot.command(name='play', aliases=['p'])
async def play(ctx, *, url=None):
    if not url:
        return await ctx.send(f"❌ `{PREFIX}play <ссылка на YouTube>`")
    
    if not ctx.author.voice:
        return await ctx.send("❌ Зайдите в голосовой канал!")
    
    if not ctx.voice_client:
        try:
            await ctx.author.voice.channel.connect()
            await asyncio.sleep(1)
        except:
            return await ctx.send("❌ Не удалось подключиться")
    
    guild_id = ctx.guild.id
    if guild_id not in queues:
        queues[guild_id] = MusicPlayer()
    
    queues[guild_id].add(url)
    
    if not ctx.voice_client.is_playing():
        await play_next(ctx)
    else:
        await ctx.send(f"✅ Добавлено в очередь ({len(queues[guild_id].queue)})")

@bot.command(name='skip')  
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Пропущено")
    else:
        await ctx.send("❌ Нечего пропускать")

@bot.command(name='stop')
async def stop(ctx):
    if ctx.guild.id in queues:
        queues[ctx.guild.id].clear()
    if ctx.voice_client:
        ctx.voice_client.stop()
    await ctx.send("⏹️ Стоп")

@bot.command(name='leave')
async def leave(ctx):
    if ctx.guild.id in queues:
        queues[ctx.guild.id].clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await ctx.send("👋 Пока!")

@bot.command(name='queue')
async def queue_cmd(ctx):
    guild_id = ctx.guild.id
    if guild_id not in queues or not queues[guild_id].queue:
        return await ctx.send("📭 Очередь пуста")
    
    text = "📋 **Очередь:**\n"
    for i, url in enumerate(queues[guild_id].queue[:10], 1):
        text += f"`{i}.` {url[:60]}\n"
    await ctx.send(text)

@bot.command(name='pause')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Пауза")

@bot.command(name='resume')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Играем")

@bot.command(name='commands')
async def commands_cmd(ctx):
    await ctx.send(f"""🎵 **Команды:**
`{PREFIX}play <url>` - Включить
`{PREFIX}skip` - Пропустить
`{PREFIX}stop` - Стоп
`{PREFIX}pause` / `{PREFIX}resume` - Пауза
`{PREFIX}queue` - Очередь
`{PREFIX}leave` - Отключить""")

if __name__ == "__main__":
    print("🚀 Запуск...")
    bot.run(TOKEN)
