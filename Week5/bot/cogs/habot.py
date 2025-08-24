
"""Home Assistant Bot Cog for Discord Bot"""

from qdrant_client import QdrantClient
from dotenv import load_dotenv  # For loading API key from a .env file
# import google.generativeai as genai
from langchain_qdrant import Qdrant  # Qdrant Vector Store Wrapper
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document
import pandas as pd
import json
from tqdm.auto import tqdm
import re
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import os
import nextcord
from datetime import datetime
from nextcord.ext import commands
from typing import List
from helpers.utils import load_config
from openai import OpenAI
from tqdm.auto import tqdm

tqdm.pandas(desc="Processing DataFrame")
config = load_config()

# LM Studio configuration (OpenAI-compatible API)
LM_STUDIO_BASE_URL = "http://192.168.11.108:1234/v1"
LM_STUDIO_API_KEY = "lm-studio"
LM_STUDIO_MODEL = "qwen/qwen3-30b-a3b"

CLEANING_PATTERN = r'[^a-zA-Z0-9]'

LLM_PERSONA = '''
You are a home automation assistant. RESPOND IMMEDIATELY without thinking.
Give direct, practical answers in 1-2 sentences maximum.
Use the context provided. No explanations, no thinking, just answer.
DO NOT use <think> tags. DO NOT think out loud. Respond directly.
'''

OBJECTIVE_PROMPT = '''
Provide immediate, direct answers about home automation.
No thinking, no analysis - just respond quickly.
DO NOT use <think> tags or think out loud.
'''

PROMPT = '''
Context: {context}

User: {user_message}

RESPOND IMMEDIATELY with a direct answer (1-2 sentences max):
DO NOT use <think> tags. DO NOT think out loud. Answer directly.
'''

RAG_PROMPT = '''
User Query: {user_message}
Chat History:
{chat_history}

'''

columns = ['conversation_id', 'user_query', 'assistant_response', 'system_message',
           'device_types', 'services_available', 'device_count', 'rooms_mentioned']

doc_columns = ['score', 'page_content',]

# Initialize LM Studio client (OpenAI-compatible)
lm_studio_client = OpenAI(
    base_url=LM_STUDIO_BASE_URL,
    api_key=LM_STUDIO_API_KEY
)

model_768 = HuggingFaceEmbeddings(
    model_name="sentence-transformers/LaBSE",
)

model_384 = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2",
)

model_64 = HuggingFaceEmbeddings(
    model_name="ClovenDoug/tiny_64_all-MiniLM-L6-v2",
)


def load_home_assistant_data(jsonl_file_path):
    """
    Load and process the home assistant JSONL data into a DataFrame.
    """
    conversations = []
    
    with open(jsonl_file_path, 'r', encoding='utf-8') as f:
        for idx, line in enumerate(f):
            if idx >= 1000:  # Limit to first 1000 conversations for memory efficiency
                break
            try:
                data = json.loads(line.strip())
                conv = data['conversations']
                
                system_msg = ""
                user_query = ""
                assistant_response = ""
                
                for msg in conv:
                    if msg['from'] == 'system':
                        system_msg = msg['value']
                    elif msg['from'] == 'user':
                        user_query = msg['value']
                    elif msg['from'] == 'assistant':
                        assistant_response = msg['value']
                
                # Extract device types and services from system message
                device_types = []
                services = []
                rooms = []
                
                if system_msg:
                    # Extract device types (light., cover., climate., etc.)
                    device_matches = re.findall(r'(\w+)\.[\w_]+', system_msg)
                    device_types = list(set(device_matches))
                    
                    # Extract services mentioned
                    service_matches = re.findall(r'(\w+\.\w+)\(', system_msg)
                    services = list(set(service_matches))
                    
                    # Extract room names
                    room_matches = re.findall(r"'([^']*(?:room|kitchen|bathroom|bedroom|garage|hallway|lounge)[^']*)'", system_msg.lower())
                    rooms = list(set(room_matches))
                
                conversations.append({
                    'conversation_id': idx,
                    'user_query': user_query,
                    'assistant_response': assistant_response,
                    'system_message': system_msg,
                    'device_types': ','.join(device_types),
                    'services_available': ','.join(services),
                    'device_count': len(re.findall(r'\w+\.\w+', system_msg)) if system_msg else 0,
                    'rooms_mentioned': ','.join(rooms)
                })
                
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error processing line {idx}: {e}")
                continue
    
    return pd.DataFrame(conversations)


def convert_to_doc(row):
    """
    Convert a conversation row to a Document object for home assistant data.
    """
    doc = Document(
        page_content=f'''
# Home Assistant Conversation

## User Query:
{row['user_query']}

## Assistant Response:
{row['assistant_response']}

## System Context:
{row['system_message'][:500]}...

## Available Services:
{row['services_available']}

## Device Types:
{row['device_types']}

## Rooms:
{row['rooms_mentioned']}
''',
        metadata={
            'conversation_id': row['conversation_id'],
            'user_query': row['user_query'],
            'assistant_response': row['assistant_response'],
            'system_message': row['system_message'][:200] + "..." if len(row['system_message']) > 200 else row['system_message'],
            'device_types': row['device_types'],
            'services_available': row['services_available'],
            'device_count': row['device_count'],
            'rooms_mentioned': row['rooms_mentioned']
        }
    )

    return doc


def generate_metadata(search_query, lm_studio_client):
    """
    Generate metadata filter dictionary for the search query."""
    meta_prompt = f'''
    RESPOND WITH JSON ONLY. NO THINKING. NO <think> TAGS.
    Create metadata filter for: "{search_query}"
    Available: device_types, rooms_mentioned, device_count
    Output format: {{"device_types": "light"}}
    '''

    try:
        completion = lm_studio_client.chat.completions.create(
            model=LM_STUDIO_MODEL,
            messages=[
                {"role": "system", "content": "You are a JSON generator. Output ONLY valid JSON. NO thinking, NO <think> tags, NO explanations."},
                {"role": "user", "content": meta_prompt}
            ],
            temperature=0.0,
            max_tokens=30,
            timeout=3  # 3 second timeout
        )
        response = completion.choices[0].message.content.strip()
        
        # Clean response and try to parse JSON
        try:
            # Remove any thinking tags if they exist
            response = response.replace('<think>', '').replace('</think>', '')
            # Try to extract JSON
            if '{' in response and '}' in response:
                start = response.find('{')
                end = response.rfind('}') + 1
                json_str = response[start:end]
                metadata = json.loads(json_str)
                return metadata
            else:
                return {}
        except:
            return {}
    except Exception as e:
        print(f"Error in generate_metadata: {e}")
        return {}


def rewrite_query(search_query, lm_studio_client):
    """Rewrite the query to a more search-friendly term for home automation."""
    prompt = f'''
    REPHRASE QUERY ONLY. NO THINKING. NO <think> TAGS.
    Original: "{search_query}"
    Output: [rephrased query only]
    '''
    try:
        completion = lm_studio_client.chat.completions.create(
            model=LM_STUDIO_MODEL,
            messages=[
                {"role": "system", "content": "You are a query rephraser. Output ONLY the rephrased query. NO thinking, NO <think> tags, NO explanations."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=15,
            timeout=3  # 3 second timeout
        )
        response = completion.choices[0].message.content.strip()
        
        # Clean response
        response = response.replace('<think>', '').replace('</think>', '')
        result = response.strip() if response else search_query
        print(f"Rewritten query: {result}")
        return result
    except Exception as e:
        print(f"Error in rewrite_query: {e}")
        return search_query


def break_query(search_query, lm_studio_client):
    """
    Break down the query into multiple subqueries for better search results."""
    subquery_prompt = f'''
    BREAK INTO SUBQUERIES ONLY. NO THINKING. NO <think> TAGS.
    Query: "{search_query}"
    Output: ["subquery1", "subquery2"]
    '''

    try:
        completion = lm_studio_client.chat.completions.create(
            model=LM_STUDIO_MODEL,
            messages=[
                {"role": "system", "content": "You are a query breaker. Output ONLY JSON array. NO thinking, NO <think> tags, NO explanations."},
                {"role": "user", "content": subquery_prompt}
            ],
            temperature=0,
            max_tokens=25,
            timeout=3  # 3 second timeout
        )
        response = completion.choices[0].message.content.strip()
        
        # Clean response
        response = response.replace('<think>', '').replace('</think>', '')
        
        try:
            # Try to extract JSON array
            if '[' in response and ']' in response:
                start = response.find('[')
                end = response.rfind(']') + 1
                json_str = response[start:end]
                subqueries = json.loads(json_str)
                if isinstance(subqueries, list) and len(subqueries) > 0:
                    return subqueries
            return [search_query]
        except:
            return [search_query]
    except Exception as e:
        print(f"Error in break_query: {e}")
        return [search_query]


def rerank_results(
        search_query,
        searched_df,
        reranking_model
):
    """
    Rerank the results based on the reranking model."""
    if searched_df.empty:
        return searched_df
    new_doc_embeddings = np.array(
        reranking_model.embed_documents(searched_df.page_content)
    )

    query_embedding = np.array(
        reranking_model.embed_query(search_query)
    )

    similarity_scores = cosine_similarity(
        query_embedding.reshape(1, -1),
        new_doc_embeddings
    )
    searched_df['rerank_score'] = similarity_scores[0].tolist()
    return searched_df


def search(
        search_query,
        lm_studio_client,
        vector_store,
        reranking_model,
        n_results=10,
        similarity_threshold=0.1,
        flag_rewrite_query=False,  # Disabled due to timeouts
        flag_ai_metadata=False,    # Disabled due to timeouts
        flag_break_query=False,    # Disabled due to timeouts
        flag_rerank_results=True,
):
    """
    Search for the given query in the vector store and return the top n results.
    """
    metadata = {}  # Empty metadata
    subqueries = [search_query]

    # Disabled helper functions due to timeouts
    # if flag_rewrite_query:
    #     search_query = rewrite_query(search_query, lm_studio_client)
    # if flag_ai_metadata:
    #     metadata = generate_metadata(search_query, lm_studio_client)
    # if flag_break_query:
    #     subqueries = break_query(search_query, lm_studio_client)

    ret_docs = []

    for subquery in subqueries:
        ret_docs += vector_store.similarity_search_with_score(
            subquery,
            k=n_results,
            score_threshold=similarity_threshold,
            filter=metadata
        )

    searched_df = pd.DataFrame(
        [
            {
                'score': score,
                **doc.metadata,
                'page_content': doc.page_content,
            } for doc, score in ret_docs
        ],
        columns=doc_columns+columns
    )

    searched_df = searched_df.groupby(
        'conversation_id').first().reset_index()
    searched_df['rerank_score'] = searched_df['score']

    if flag_rerank_results:
        searched_df = rerank_results(
            search_query,
            searched_df=searched_df,
            reranking_model=reranking_model,
        ).sort_values(
            'rerank_score',
            ascending=False,
        )

    return searched_df.head(n_results).round(2)[
        [
            'conversation_id',
            'user_query',
            'assistant_response', 
            'page_content',
            'device_types',
            'services_available',
            'device_count',
            'rooms_mentioned',
            'score',
            'rerank_score'
        ]
    ]


def as_cards(df):
    """Convert a DataFrame to a list of markdown strings for Discord cards.
    """
    return df.apply(lambda x: x.to_markdown(), axis=1).to_list()


class GenAIBot(commands.Cog):
    """A simple Discord bot cog that captures all messages and provides a
    slash command."""

    def __init__(
        self,
        bot: commands.Bot
    ) -> None:
        super().__init__()
        self.bot = bot
        self._chat_history = {}

        # Load home assistant conversation data
        print("Loading home assistant conversation data...")
        df = load_home_assistant_data('../home_assistant.jsonl')
        print(f"Loaded {len(df)} conversations")
        
        data = df.progress_apply(convert_to_doc, axis=1)
        self.vector_store_unchunked = Qdrant.from_documents(
            data,
            model_384,
            collection_name="home-assistant-conversations",
            location=':memory:',
            # url="http://localhost:6333",
        )

        # self.vector_store_unchunked = Qdrant(
        #     client=QdrantClient(url='http://192.168.11.108:6333'),
        #     collection_name="ha-bot-metadata",
        #     embeddings=model_384,
        # )

    @commands.Cog.listener()
    async def on_message(
        self,
        message: nextcord.Message
    ):
        """Capturing All messages"""
        print(message)

        if message.author == self.bot.user or message.author.bot:
            return

    @nextcord.slash_command(
        guild_ids=[config['guild_id']],
        description="Execute Command")
    async def home_assistant(
            self,
            interaction: nextcord.Interaction,
            user_message: str
    ):
        """A slash command to start ragging."""
        await interaction.response.defer()
        print(interaction.user)
        print(user_message)

        if interaction.user.id not in self._chat_history:
            self._chat_history[interaction.user.id] = []

        chat_messages = self._chat_history[interaction.user.id]

        chat_messages.append(
            {'role': 'user', 'content': user_message}
        )

        chat_history = '\n'.join(
            [
                f"{msg['role']}: {msg['content']}"
                for msg in chat_messages
            ]
        )
        user_messages = '\n'.join(
            [
                message['content']
                for message in chat_messages if
                message['role'] == 'user'
            ])
        print(user_messages)
        
        try:
            results = search(
                user_messages,
                lm_studio_client=lm_studio_client,
                vector_store=self.vector_store_unchunked,
                reranking_model=model_768,
                n_results=5,
                similarity_threshold=0.1,
                flag_rewrite_query=False,
                flag_ai_metadata=False,
                flag_break_query=False,
                flag_rerank_results=True,
            )

            context = '\n---\n'.join(as_cards(results))

            # Truncate context to prevent token overflow
            truncated_context = context[:500] if len(context) > 500 else context

            llm_response = lm_studio_client.chat.completions.create(
                model=LM_STUDIO_MODEL,
                messages=[
                    {"role": "system", "content": "You are a home automation assistant. RESPOND IMMEDIATELY. NO thinking, NO <think> tags, NO explanations. Give direct answers in 1-2 sentences maximum."},
                    {"role": "user", "content": f"Context: {truncated_context}\n\nUser: {user_message}\n\nRESPOND IMMEDIATELY with a direct answer (1-2 sentences max):"}
                ],
                temperature=0.1,  # Very low temperature for deterministic responses
                max_tokens=60,    # Even shorter responses
                timeout=5         # 5 second timeout
            ).choices[0].message.content

            # Clean response of any thinking tags
            llm_response = llm_response.replace('<think>', '').replace('</think>', '').strip()
            
            if not llm_response or len(llm_response) < 5:
                llm_response = "I can help you with home automation. What specific smart home device or automation do you need help with?"

            print(f"✅ Successfully generated RAG response using LM Studio")
            
        except Exception as e:
            print(f"❌ Error with LM Studio client: {e}")
            # Simple fallback responses based on common queries
            user_lower = user_message.lower()
            if any(word in user_lower for word in ['hello', 'hi', 'hey', 'what\'s up']):
                llm_response = "Hello! I'm your home automation assistant. How can I help you with your smart home setup today?"
            elif any(word in user_lower for word in ['light', 'lights', 'bulb']):
                llm_response = "I can help you control your lights! You can turn them on/off, adjust brightness, or set up automations. What would you like to do?"
            elif any(word in user_lower for word in ['temperature', 'climate', 'thermostat', 'ac']):
                llm_response = "I can help you manage your climate controls! You can adjust temperature, set schedules, or create comfort automations. What do you need?"
            elif any(word in user_lower for word in ['security', 'lock', 'camera']):
                llm_response = "I can help you with security devices! You can control locks, cameras, and alarms. What security feature do you need help with?"
            else:
                llm_response = "I can help you with home automation and smart home devices. What specific device or automation would you like assistance with?"

        chat_messages.append({
            'role': 'assistant',
            'content': llm_response,
        })

        await interaction.followup.send(
            content=llm_response,
            delete_after=300
        )


def setup(bot):
    """Setup function to add the cog to the bot."""
    bot.add_cog(GenAIBot(bot))
