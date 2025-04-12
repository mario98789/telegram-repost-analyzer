
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
from telethon.tl.functions.channels import GetFullChannelRequest

st.set_page_config(page_title="Telegram Репост Анализатор", layout="wide")
st.title("Telegram Репост Анализатор")
st.markdown("Анализ репостов в Telegram каналах. Введите до 50 ссылок и получите список оригинальных каналов.")

if 'temp_dir' not in st.session_state:
    st.session_state.temp_dir = tempfile.mkdtemp()
    st.session_state.session_files = []

def cleanup():
    if 'temp_dir' in st.session_state and os.path.exists(st.session_state.temp_dir):
        shutil.rmtree(st.session_state.temp_dir)

import atexit
atexit.register(cleanup)

uploaded_file = st.file_uploader("Загрузите ZIP-архив с .session файлами", type="zip")

if uploaded_file is not None:
    zip_path = os.path.join(st.session_state.temp_dir, "sessions.zip")
    with open(zip_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(st.session_state.temp_dir)
    st.session_state.session_files = [f for f in os.listdir(st.session_state.temp_dir) if f.endswith('.session')]
    if st.session_state.session_files:
        st.success(f"Архив успешно распакован. Найдено {len(st.session_state.session_files)} .session файлов.")
    else:
        st.error("В архиве не найдено .session файлов.")

if st.session_state.session_files:
    selected_sessions = st.multiselect(
        "Выберите .session файлы для анализа",
        st.session_state.session_files,
        default=st.session_state.session_files[:1]
    )

    # Ввод нескольких ссылок
    raw_links = st.text_area("Вставьте до 50 ссылок на Telegram-каналы (по одной на строке):")
    max_messages = st.number_input("Сколько сообщений анализировать:", min_value=10, max_value=1000, value=100)
    run_button = st.button("Запустить анализ")

    if run_button and raw_links and selected_sessions:
        input_links = list(set([line.strip() for line in raw_links.splitlines() if line.strip()]))[:50]

        # Нормализация username'ов
        def extract_channel_name(link):
            if link.startswith("https://t.me/"):
                return link.split("https://t.me/")[1].split("/")[0]
            elif link.startswith("http://t.me/"):
                return link.split("http://t.me/")[1].split("/")[0]
            elif link.startswith("t.me/"):
                return link.split("t.me/")[1].split("/")[0]
            elif link.startswith("@"):
                return link[1:]
            else:
                return link

        channel_list = [extract_channel_name(link) for link in input_links]

        progress_bar = st.progress(0)
        status_text = st.empty()
        results = []

        async def analyze_channel(session_path, channel, limit):
            session_name = os.path.splitext(os.path.basename(session_path))[0]
            full_session_path = os.path.join(st.session_state.temp_dir, session_name)
            client = TelegramClient(full_session_path, api_id=123456, api_hash="0123456789abcdef0123456789abcdef")
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    return []
                session_results = []
                async for message in client.iter_messages(channel, limit=limit):
                    if message.fwd_from and hasattr(message.fwd_from, 'from_id') and message.fwd_from.from_id:
                        if isinstance(message.fwd_from.from_id, PeerChannel):
                            original_channel_id = message.fwd_from.from_id.channel_id
                            try:
                                original = await client.get_entity(PeerChannel(original_channel_id))
                                original_title = original.title
                                original_link = f"https://t.me/{original.username}" if original.username else f"[Private Channel | ID: {original_channel_id}]"

                                full_info = await client(GetFullChannelRequest(original))
                                participant_count = getattr(full_info.full_chat, 'participants_count', 0)
                                if participant_count < 1500:
                                    continue

                            except Exception:
                                original_title = "Неизвестный канал"
                                original_link = f"[ID: {original_channel_id}]"
                            session_results.append({
                                "Оригинальный канал": original_title,
                                "Ссылка": original_link,
                                "Текст": message.text[:200] + ('...' if message.text and len(message.text) > 200 else ''),
                                "Дата": message.date.strftime("%Y-%m-%d %H:%M:%S")
                            })
                return session_results
            except SessionPasswordNeededError:
                return []
            except Exception:
                return []
            finally:
                await client.disconnect()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        tasks = []
        for session in selected_sessions:
            for channel in channel_list:
                session_path = os.path.join(st.session_state.temp_dir, session)
                tasks.append(analyze_channel(session_path, channel, max_messages))

        results_nested = loop.run_until_complete(asyncio.gather(*tasks))
        for res in results_nested:
            results.extend(res)
        progress_bar.progress(1.0)

        if results:
            df = pd.DataFrame(results)
            df = df.drop_duplicates()

            st.success(f"Найдено {len(df)} репостов из {df['Оригинальный канал'].nunique()} уникальных каналов.")
            st.subheader("Топ каналов по количеству репостов")
            top_channels = df['Оригинальный канал'].value_counts().reset_index()
            top_channels.columns = ['Канал', 'Количество репостов']
            st.dataframe(top_channels.head(10))

            st.subheader("Все найденные репосты")
            st.dataframe(df)

            st.subheader("📋 Чистый список оригинальных Telegram-ссылок")
            links_only = sorted(set([x for x in df['Ссылка'].tolist() if x.startswith("https://t.me/")]))
            st.code("\n".join(links_only), language='text')

            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button("Скачать CSV", csv, "reposts.csv", "text/csv")
        else:
            st.warning("Репосты не найдены или произошли ошибки во всех сессиях.")
