import time
import requests
import streamlit as st


# Streamed response emulator from local Ollama.
def response_generator(prompt):
    """
    Install Ollama locally and thrn run `ollama pull llama3:instruct`
    """
    url = "http://localhost:11434/api/chat"
    model = "llama3:instruct"
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "stream": False
    }
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        for word in response.json()["message"]["content"].split():
            yield word + " "
            time.sleep(0.05)
    else:
        return f"Error: {response.status_code} - {response.text}"
   


st.title("Dhara's Local Bot")

# Custom CSS for chat alignment
st.markdown("""
    <style>
    /* User message: align right */
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        flex-direction: row-reverse;
        text-align: right;
    }

    /* Assistant message: align left */
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
        flex-direction: row;
    }
    </style>
""", unsafe_allow_html=True)

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display Chats from history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# React to user input
if prompt := st.chat_input("What is up?"):
    # Display user message in chat message container
    st.chat_message("user").markdown(prompt)
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    response = ""
    # Display assistant response in chat message container
    with st.chat_message("assistant"), st.spinner("🤔 Thinking..."):
        response = st.write_stream(stream=response_generator(prompt))
    # Add assistant response to chat history
    st.session_state.messages.append({"role": "assistant", "content": response})
