import os
import random
import requests
import streamlit as st
import pdfplumber
import subprocess
import tempfile
from pydub import AudioSegment


def macos_tts(text):
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as fp:
        subprocess.run(['say', '-o', fp.name, text])
        # print("AIFF path:", fp.name)
        # print("File size:", os.path.getsize(fp.name))
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_fp:
            sound = AudioSegment.from_file(fp.name, format="aiff")
            sound.export(wav_fp.name, format="wav")
            return wav_fp.name


def extract_text(pdf_file):
    text = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


def summarize_with_ollama(text, model="llama3:instruct", system_prompt=None):
    """
    Install Ollama locally and thrn run `ollama pull llama3:instruct`
    """
    url = "http://localhost:11434/api/chat"
    if not system_prompt:
        system_prompt = "You are a helpful assistant. Summarize the following PDF content."
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        "stream": False
    }
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        return response.json()["message"]["content"]
    else:
        return f"Error: {response.status_code} - {response.text}"


st.title("TL;DR PDF")
st.write("Upload your big PDF here. It will analyze and summarize the content for you. ")


st.subheader("Upload PDF")
uploaded_pdf = st.file_uploader("Upload Pdf here", type="pdf")

if uploaded_pdf is not None:    
    if "text" not in st.session_state or st.session_state.text is None:
        with st.spinner("Reading..."):
            st.session_state.text = extract_text(uploaded_pdf)
    
    current_few = random.randint(50, 100)
    st.subheader("Extracted Text (first %s chars)" % current_few)
    st.write(st.session_state.text[:current_few] + "..." if len(st.session_state.text) > current_few else st.session_state.text)
    
    if "summary" not in st.session_state or st.session_state.summary is None:
        with st.spinner("Summarizing PDF..."):
            st.session_state.summary = summarize_with_ollama(st.session_state.text)
    st.subheader("Summary:")
    st.write(st.session_state.summary)
else:
    st.session_state.summary = None
    
if "summary" in st.session_state and st.session_state.summary is not None and st.button("🔊 Listen."):
    st.subheader("Now, listen to it.")
    with st.spinner("Generating Audio..."):
        wav_path = macos_tts(st.session_state.summary)
        with open(wav_path, "rb") as audio_file:
            audio_bytes = audio_file.read()
            st.audio(audio_bytes, format="audio/wav")
