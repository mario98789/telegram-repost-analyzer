import streamlit as st
import asyncio
import pandas as pd
import zipfile
import tempfile
import os
import shutil
from telethon.sync import TelegramClient
from telethon.tl.types import PeerChannel
from telethon.errors import SessionPasswordNeededError

# Настройка страницы
st.set_page_config(page_title="Telegram Репост Анализатор", layout="wide")

# Заголовок приложения
st.title("Telegram Репост Анализатор")
st.markdown("Анализ репостов в Telegram каналах с использованием `.session` файлов")

# Создаем временную директорию для хранения сессий
if 'temp_dir' not in st.session_state:
    st.session_state.temp_dir = tempfile.mkdtemp()
    st.session_state.session_files = []

# Функция для очистки временных файлов при закрытии приложения
def cleanup():
    if 'temp_dir' in st.session_state and os.path.exists(st.session_state.temp_dir):
        shutil.rmtree(st.session_state.temp_dir)

# Регистрируем функцию очистки
import atexit
atexit.register(cleanup)

# Загрузка ZIP-файла с сессиями
uploaded_file = st.file_uploader("Загрузите ZIP-архив с .session файлами", type="zip")

if uploaded_file is not None:
    # Сохраняем загруженный файл во временную директорию
    zip_path = os.path.join(st.session_state.temp_dir, "sessions.zip")
    with open(zip_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    
    # Распаковываем архив
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(st.session_state.temp_dir)
    
    # Получаем список .session файлов
    st.session_state.session_files = [f for f in os.listdir(st.session_state.temp_dir) 
                                     if f.endswith('.session')]
    
    if st.session_state.session_files:
        st.success(f"Архив успешно распакован. Найдено {len(st.session_state.session_files)} .session файлов.")
    else:
        st.error("В архиве не найдено .session файлов.")

# Выбор сессий для анализа
if st.session_state.session_files:
    selected_sessions = st.multiselect(
        "Выберите .session файлы для анализа",
        st.session_state.session_files,
        default=st.session_state.session_files[:1]  # По умолчанию выбираем первый файл
    )
    
    # Параметры анализа
    col1, col2 = st.columns(2)
    with col1:
        channel_input = st.text_input("Введите username или ссылку на Telegram-канал (например, @durov):")
    with col2:
        max_messages = st.number_input("Сколько сообщений анализировать:", 
                                      min_value=10, max_value=1000, value=100)
    
    # Кнопка запуска анализа
    run_button = st.button("Запустить анализ")
    
    if run_button and channel_input and selected_sessions:
        # Подготовка для анализа
        progress_bar = st.progress(0)
        status_text = st.empty()
        results = []
        
        # Функция для анализа канала с использованием сессии
        async def analyze_channel(session_path, channel, limit):
            # Извлекаем имя сессии без расширения для Telethon
            session_name = os.path.splitext(os.path.basename(session_path))[0]
            full_session_path = os.path.join(st.session_state.temp_dir, session_name)
            
            # Создаем клиент с пустыми API ID и hash (будут использоваться из сессии)
            client = TelegramClient(full_session_path, api_id=123456, api_hash="0123456789abcdef0123456789abcdef")
            
            try:
                await client.connect()
                
                # Проверяем, авторизован ли клиент
                if not await client.is_user_authorized():
                    status_text.warning(f"Сессия {session_name} не авторизована. Пропускаем.")
                    return []
                
                # Нормализуем ввод канала (убираем @ и t.me/ если есть)
                if channel.startswith('@'):
                    channel = channel[1:]
                elif 't.me/' in channel:
                    channel = channel.split('t.me/')[1]
                
                session_results = []
                try:
                    # Получаем сообщения из канала
                    async for message in client.iter_messages(channel, limit=limit):
                        # Проверяем, является ли сообщение репостом
                        if message.fwd_from and hasattr(message.fwd_from, 'from_id') and message.fwd_from.from_id:
                            try:
                                # Пытаемся получить информацию об оригинальном канале
                                if isinstance(message.fwd_from.from_id, PeerChannel):
                                    original_channel_id = message.fwd_from.from_id.channel_id
                                    try:
                                        original = await client.get_entity(PeerChannel(original_channel_id))
                                        original_title = original.title
                                        original_link = f"https://t.me/{original.username}" if original.username else f"[Private Channel | ID: {original_channel_id}]"
                                    except Exception:
                                        original_title = "Неизвестный канал"
                                        original_link = f"[ID: {original_channel_id}]"
                                    
                                    # Добавляем результат
                                    session_results.append({
                                        "Оригинальный канал": original_title,
                                        "Ссылка": original_link,
                                        "Текст": message.text[:200] + ('...' if len(message.text) > 200 else ''),
                                        "Дата": message.date.strftime("%Y-%m-%d %H:%M:%S")
                                    })
                            except Exception as e:
                                status_text.warning(f"Ошибка при обработке сообщения: {str(e)}")
                except Exception as e:
                    status_text.warning(f"Ошибка при получении сообщений из канала {channel}: {str(e)}")
                
                return session_results
                
            except SessionPasswordNeededError:
                status_text.warning(f"Сессия {session_name} требует двухфакторную авторизацию. Пропускаем.")
                return []
            except Exception as e:
                status_text.warning(f"Ошибка при подключении через {session_name}: {str(e)}")
                return []
            finally:
                await client.disconnect()
        
        # Запускаем анализ для всех выбранных сессий
        status_text.info("Анализируем каналы... Это может занять некоторое время.")
        
        # Создаем и запускаем цикл событий
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Запускаем задачи анализа для каждой сессии
        tasks = []
        for i, session in enumerate(selected_sessions):
            session_path = os.path.join(st.session_state.temp_dir, session)
            task = analyze_channel(session_path, channel_input, max_messages)
            tasks.append(task)
            progress_bar.progress((i + 1) / len(selected_sessions) / 2)  # Первая половина прогресса - подготовка задач
        
        # Выполняем все задачи и собираем результаты
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        completed_tasks = loop.run_until_complete(asyncio.gather(*tasks))
        for i, session_results in enumerate(completed_tasks):
            results.extend(session_results)
            progress_bar.progress(0.5 + (i + 1) / len(selected_sessions) / 2)  # Вторая половина прогресса - обработка результатов
        
        # Отображаем результаты
        if results:
            # Создаем DataFrame из результатов
            df = pd.DataFrame(results)
            
            # Удаляем дубликаты
            df = df.drop_duplicates()
            
            # Отображаем статистику
            st.success(f"Анализ завершен! Найдено {len(df)} репостов из {df['Оригинальный канал'].nunique()} уникальных каналов.")
            
            # Отображаем топ каналов по количеству репостов
            st.subheader("Топ каналов по количеству репостов")
            top_channels = df['Оригинальный канал'].value_counts().reset_index()
            top_channels.columns = ['Канал', 'Количество репостов']
            st.dataframe(top_channels.head(10))
            
            # Отображаем все результаты
            st.subheader("Все найденные репосты")
            st.dataframe(df)
            
            # Кнопка для скачивания CSV
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="Скачать результаты (CSV)",
                data=csv,
                file_name=f"telegram_reposts_{channel_input.replace('@', '')}.csv",
                mime="text/csv"
            )
        else:
            st.warning("Репосты не найдены или произошли ошибки во всех сессиях.")
        
        # Очищаем прогресс-бар и статус
        progress_bar.empty()
        status_text.empty()

# Инструкции по использованию
with st.expander("Инструкция по использованию"):
    st.markdown("""
    ### Как использовать приложение:
    
    1. **Подготовка сессий**:
       - Вам понадобятся `.session` файлы Telegram (файлы авторизации)
       - Упакуйте их в ZIP-архив
    
    2. **Загрузка и анализ**:
       - Загрузите ZIP-архив с `.session` файлами
       - Выберите нужные сессии из списка
       - Введите имя канала или ссылку на него
       - Укажите количество сообщений для анализа
       - Нажмите "Запустить анализ"
    
    3. **Результаты**:
       - Просмотрите статистику по найденным репостам
       - Изучите таблицу с детальной информацией
       - Скачайте результаты в формате CSV
    
    ### Примечания:
    - Приложение работает только с валидными `.session` файлами
    - Для анализа закрытых каналов необходимо, чтобы аккаунт имел доступ к этому каналу
    - Все данные обрабатываются локально и не отправляются на сторонние серверы
    """)

# Информация о приложении в сайдбаре
with st.sidebar:
    st.subheader("О приложении")
    st.markdown("""
    **Telegram Репост Анализатор** - инструмент для анализа репостов в Telegram каналах.
    
    Приложение позволяет:
    - Анализировать публичные и приватные каналы
    - Использовать несколько сессий одновременно
    - Находить источники репостов
    - Экспортировать результаты в CSV
    
    *Приложение использует библиотеку Telethon для взаимодействия с Telegram API.*
    """)
