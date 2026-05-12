# discord_scheduler_v2.py (финальная версия с надежным планированием)
import discord
import asyncio
import datetime
import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, List, Optional

# Загружаем .env файл
env_path = Path('.env')
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    print(f"✅ Файл .env загружен из: {env_path.absolute()}")

CONFIG_FILE = "config.json"
MSK_OFFSET = 3

# ID ботов и их токены
BOTS_CONFIG = [
    {"id": 1490385281654853805, "token_env": "DISCORD_TOKEN_1"},
    {"id": 1487889737858552040, "token_env": "DISCORD_TOKEN_2"},
    {"id": 1246479550746202157, "token_env": "DISCORD_TOKEN_3"},
]

class BotInstance:
    def __init__(self, bot_id: int, token: str, bot_index: int, total_bots: int, channels_config: List[Dict]):
        self.bot_id = bot_id
        self.token = token
        self.bot_index = bot_index
        self.total_bots = total_bots
        self.channels_config = channels_config
        self.client = None
        self.is_running = False
        self.channel_tasks = {}  # Храним задачи для каждого канала
        
    def get_current_time(self):
        return datetime.datetime.utcnow()
    
    def get_msk_time(self, dt=None):
        if dt is None:
            dt = self.get_current_time()
        return dt + datetime.timedelta(hours=MSK_OFFSET)
    
    def format_time(self, seconds):
        if seconds < 0:
            seconds = 0
        if seconds < 60:
            return f"{seconds:.0f} сек"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f} мин"
        else:
            hours = seconds / 3600
            return f"{hours:.1f} ч"
    
    def calculate_bot_interval(self, slowmode_seconds: int) -> float:
        return slowmode_seconds / self.total_bots
    
    def update_channel_config(self, channel_id: int, key: str, value):
        config = load_global_config()
        for channel in config["channels"]:
            if channel["channel_id"] == channel_id:
                channel[key] = value
                break
        save_global_config(config)
        
        for channel in self.channels_config:
            if channel["channel_id"] == channel_id:
                channel[key] = value
                break
    
    def should_bot_send_to_channel(self, channel_config: Dict) -> bool:
        """
        Проверяет, должен ли этот бот отправлять сообщения в данный канал.
        Если в конфиге канала указан bot_indices, то отправляет только бот с индексом из списка.
        Если bot_indices не указан или пуст, отправляют все боты.
        """
        bot_indices = channel_config.get("bot_indices", [])
        if not bot_indices:
            return True  # Отправляют все боты
        return self.bot_index in bot_indices
    
    async def send_message(self, channel_config: Dict):
        channel_id = channel_config.get("channel_id")
        message_text = channel_config.get("message")
        image_path = channel_config.get("image_path")
        slowmode = channel_config.get("slowmode_seconds", 21600)
        
        channel = self.client.get_channel(channel_id)
        if not channel:
            print(f'❌ Бот #{self.bot_index + 1} | Канал {channel_id} не найден')
            return False
        
        try:
            current_msk = self.get_msk_time()
            
            if image_path and os.path.exists(image_path):
                with open(image_path, 'rb') as f:
                    await channel.send(
                        content=message_text,
                        file=discord.File(f, os.path.basename(image_path))
                    )
                print(f'✅ Бот #{self.bot_index + 1} | [{current_msk.strftime("%H:%M:%S")} МСК] Сообщение + ФАЙЛ в канал {channel.name}')
            else:
                if image_path and not os.path.exists(image_path):
                    print(f'   ⚠️ Бот #{self.bot_index + 1} | Файл не найден: {image_path}')
                await channel.send(message_text)
                print(f'✅ Бот #{self.bot_index + 1} | [{current_msk.strftime("%H:%M:%S")} МСК] Сообщение в канал {channel.name}')
            
            # Обновляем время и индекс бота
            current_time_utc = self.get_current_time()
            channel_config["last_message_time"] = current_time_utc.isoformat()
            channel_config["last_bot_index"] = self.bot_index
            self.update_channel_config(channel_id, "last_message_time", current_time_utc.isoformat())
            self.update_channel_config(channel_id, "last_bot_index", self.bot_index)
            
            return True
            
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = e.retry_after if hasattr(e, 'retry_after') else 60
                print(f'⏳ Бот #{self.bot_index + 1} | Rate limit в канале {channel_id}, повтор через {self.format_time(retry_after)}')
                await asyncio.sleep(retry_after)
                return await self.send_message(channel_config)
            else:
                print(f'❌ Бот #{self.bot_index + 1} | Ошибка отправки в канал {channel_id}: {e}')
                return False
        except Exception as e:
            print(f'❌ Бот #{self.bot_index + 1} | Ошибка отправки в канал {channel_id}: {e}')
            return False
    
    async def channel_scheduler(self, channel_config: Dict):
        """Планировщик для канала - запускается один раз и работает постоянно"""
        channel_id = channel_config.get("channel_id")
        slowmode = channel_config.get("slowmode_seconds", 21600)
        channel = self.client.get_channel(channel_id)
        channel_name = channel.name if channel else str(channel_id)
        
        # Проверяем, должен ли этот бот отправлять в этот канал
        if not self.should_bot_send_to_channel(channel_config):
            print(f'⏭️ Бот #{self.bot_index + 1} | Канал {channel_name} - пропускаем (бот не в списке bot_indices)')
            return
        
        print(f'📋 Бот #{self.bot_index + 1} | Запущен планировщик для канала {channel_name}')
        
        while self.is_running:
            try:
                # Рассчитываем время до следующей отправки
                last_time_str = channel_config.get("last_message_time")
                last_bot_index = channel_config.get("last_bot_index", -1)
                current_time = self.get_current_time()
                
                if not last_time_str or last_bot_index == -1:
                    # Нет истории - определяем начальную позицию
                    wait_time = self.calculate_bot_interval(slowmode) * self.bot_index
                    if wait_time > 0:
                        print(f'⏰ Бот #{self.bot_index + 1} | Канал {channel_name} | Первый запуск через {self.format_time(wait_time)}')
                        await asyncio.sleep(wait_time)
                    
                    # Отправляем
                    success = await self.send_message(channel_config)
                    if success:
                        # После отправки ждем полный цикл
                        await asyncio.sleep(slowmode)
                    else:
                        await asyncio.sleep(60)
                    continue
                
                # Есть история - определяем нашу очередь
                last_time = datetime.datetime.fromisoformat(last_time_str)
                bot_interval = self.calculate_bot_interval(slowmode)
                
                # Вычисляем следующее время отправки для этого бота
                # Формула: время_последней_отправки + (интервал_между_ботами * (индекс_бота - индекс_последнего_бота + total_bots) % total_bots + 1)
                offset = (self.bot_index - last_bot_index - 1 + self.total_bots) % self.total_bots + 1
                next_send_time = last_time + datetime.timedelta(seconds=bot_interval * offset)
                
                # Если следующее время уже прошло, добавляем slowmode до будущего
                while next_send_time <= current_time:
                    next_send_time += datetime.timedelta(seconds=slowmode)
                
                # Ждем до следующей отправки
                wait_seconds = (next_send_time - current_time).total_seconds()
                
                if wait_seconds > 0:
                    next_time_msk = self.get_msk_time(next_send_time)
                    print(f'⏰ Бот #{self.bot_index + 1} | Канал {channel_name} | Следующая отправка в {next_time_msk.strftime("%H:%M:%S")} МСК (через {self.format_time(wait_seconds)})')
                    await asyncio.sleep(wait_seconds)
                
                # Отправляем сообщение
                success = await self.send_message(channel_config)
                
                if not success:
                    # Если не отправилось, ждем минуту и пробуем снова
                    await asyncio.sleep(60)
                
            except Exception as e:
                print(f'❌ Бот #{self.bot_index + 1} | Ошибка в планировщике канала {channel_name}: {e}')
                await asyncio.sleep(60)
    
    async def run(self):
        """Запуск бота"""
        self.is_running = True
        
        # Для discord.py-self
        self.client = discord.Client()
        
        @self.client.event
        async def on_ready():
            print(f'✅ Бот #{self.bot_index + 1} (ID: {self.bot_id}) успешно запущен!')
            print(f'📱 Аккаунт: {self.client.user} (ID: {self.client.user.id})')
            
            current_msk = self.get_msk_time()
            print(f'🕒 Текущее МСК: {current_msk.strftime("%H:%M:%S")}')
            
            # Фильтруем каналы, в которые этот бот должен отправлять
            my_channels = [ch for ch in self.channels_config 
                          if ch.get("enabled", True) and self.should_bot_send_to_channel(ch)]
            
            print(f'📊 Всего каналов в конфиге: {len(self.channels_config)}')
            print(f'📊 Каналов для бота #{self.bot_index + 1}: {len(my_channels)}')
            print(f'🔢 Порядковый номер бота в цикле: {self.bot_index + 1} из {self.total_bots}\n')
            
            print('🔍 Запуск планировщиков для каналов...\n')
            
            for channel_config in self.channels_config:
                if not channel_config.get("enabled", True):
                    continue
                
                # Пропускаем каналы, в которые этот бот не должен отправлять
                if not self.should_bot_send_to_channel(channel_config):
                    continue
                    
                channel_id = channel_config.get("channel_id")
                channel = self.client.get_channel(channel_id)
                slowmode = channel_config.get("slowmode_seconds", 21600)
                
                if not channel:
                    print(f'❌ Канал {channel_id} не найден для бота #{self.bot_index + 1}')
                    continue
                
                print(f'📢 Канал: {channel.name} (ID: {channel_id})')
                print(f'   Slowmode: {self.format_time(slowmode)}')
                print(f'   Бот #{self.bot_index + 1} будет отправлять каждые {self.format_time(slowmode)} (каждый {self.bot_index + 1}-й в очереди)')
                
                # Запускаем планировщик для канала
                task = asyncio.create_task(self.channel_scheduler(channel_config))
                self.channel_tasks[channel_id] = task
                print()
            
            print(f'🟢 Бот #{self.bot_index + 1} запущен, все планировщики активны!\n')
        
        @self.client.event
        async def on_message(message):
            pass
        
        try:
            await self.client.start(self.token)
        except Exception as e:
            print(f'❌ Ошибка запуска бота #{self.bot_index + 1}: {e}')
            self.is_running = False
        finally:
            # Отменяем все задачи при остановке
            for task in self.channel_tasks.values():
                task.cancel()

def load_global_config():
    if Path(CONFIG_FILE).exists():
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        default_config = {
            "channels": [],
            "send_report_on_start": False
        }
        save_global_config(default_config)
        return default_config

def save_global_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def get_tokens():
    tokens = []
    for bot_config in BOTS_CONFIG:
        token = os.getenv(bot_config["token_env"])
        if token:
            tokens.append({
                "id": bot_config["id"],
                "token": token,
                "env_name": bot_config["token_env"]
            })
        else:
            print(f"⚠️ Токен для бота {bot_config['id']} не найден")
    return tokens

async def run_bot_instance(bot_id: int, token: str, bot_index: int, total_bots: int, channels_config: List[Dict]):
    bot = BotInstance(bot_id, token, bot_index, total_bots, channels_config)
    await bot.run()

async def main_async():
    print("=" * 60)
    print("🤖 Discord Multi-Bot Scheduler v5 (с поддержкой выборочной отправки)")
    print("=" * 60)
    
    config = load_global_config()
    channels_config = config.get("channels", [])
    
    if not channels_config:
        print("❌ Нет настроенных каналов")
        return
    
    tokens = get_tokens()
    total_bots = len(tokens)
    
    if total_bots == 0:
        print("❌ Нет токенов!")
        return
    
    print(f"\n✅ Найдено ботов: {total_bots}")
    for i, t in enumerate(tokens):
        masked = t["token"][:5] + "..." + t["token"][-5:] if len(t["token"]) > 10 else "***"
        print(f"   Бот #{i+1}: ID={t['id']}, Токен={masked}")
    
    print(f"\n📊 Настроено каналов: {len(channels_config)}")
    
    # Показываем расписание и настройки отправки для каждого канала
    print("\n📋 Конфигурация каналов:")
    current_time = datetime.datetime.utcnow()
    
    for channel in channels_config:
        if channel.get("enabled", True):
            channel_id = channel.get("channel_id")
            slowmode = channel.get("slowmode_seconds", 21600)
            bot_indices = channel.get("bot_indices", [])
            bot_interval = slowmode / total_bots if total_bots > 0 else slowmode
            
            print(f"\n   Канал: {channel_id}")
            print(f"   Slowmode: {slowmode//3600}ч {slowmode%3600//60}мин")
            
            if bot_indices:
                bot_numbers = [f"#{i+1}" for i in bot_indices]
                print(f"   Отправляют только боты: {', '.join(bot_numbers)}")
            else:
                print(f"   Отправляют все боты (1-{total_bots})")
            
            print(f"   Интервал между отправками одного бота: {slowmode//3600}ч {slowmode%3600//60}мин")
            
            last_time_str = channel.get("last_message_time")
            last_bot_index = channel.get("last_bot_index", -1)
            
            if last_time_str and last_bot_index != -1 and (not bot_indices or last_bot_index in bot_indices):
                last_time = datetime.datetime.fromisoformat(last_time_str)
                last_time_msk = last_time + datetime.timedelta(hours=MSK_OFFSET)
                print(f"   Последняя отправка: {last_time_msk.strftime('%H:%M:%S')} (бот #{last_bot_index + 1})")
    
    print("\n🔄 Запуск ботов...\n")
    
    tasks = []
    for i, token_info in enumerate(tokens):
        task = asyncio.create_task(
            run_bot_instance(
                token_info["id"],
                token_info["token"],
                i,
                total_bots,
                channels_config
            )
        )
        tasks.append(task)
    
    await asyncio.gather(*tasks)

def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n👋 Остановлено пользователем.")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        time.sleep(60)

if __name__ == "__main__":
    main()