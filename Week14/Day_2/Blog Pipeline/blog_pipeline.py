# LangGraph-based Blog Automation - FIXED VERSION

from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Optional, Dict
from playwright.async_api import async_playwright
import asyncio
import time
import requests
import openai
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from browser_use import Agent, BrowserConfig, Browser, BrowserSession
import ast
from dotenv import load_dotenv
import os

load_dotenv()

'''
Team of 7 AI Agents writing and distributing technical blogs:

Agent 1: Controls my computer and scrapes blog titles from Dev to
Agent 2: Extracts and displays multiple blog ideas from the home feed,
         then selects one idea at a time for processing
Agent 3: Uses Perplexity to research the selected blog topic in-depth
Agent 4: Converts the research into a full-length technical blog post
Agent 5: Reviews the blog for grammar, structure, technical accuracy, and tone
Agent 6: Converts the blog into Reddit + Hacker News format for social distribution
Agent 7: Controls my computer again to log into Dev to and draft the final blog post
'''

browser_session = BrowserSession(user_data_dir= None) #for 1st step
browser_session1 = BrowserSession(allowed_domains=['https://dev.to'], user_data_dir= None) #for last step

# For Medium Agent
openai_api_key_breakdown    = os.getenv("OPENAI_API_KEY")       # For task breakdown
openai_api_key_medium       = os.getenv("OPENAI_API_KEY")          # For running the Medium agent
openai_api_key_extraction   = os.getenv("OPENAI_API_MY_KEY")      # For extracting the final output
gemini_key = os.getenv("GEMINI_KEY")          # For Gemini API

# For Content Selector Agent
openai_api_key_content_selector = os.getenv("OPENAI_API_MY_KEY")

# For Blog Writing Agent
openai_api_key_blog = os.getenv("OPENAI_API_MY_KEY")

# For Social Media Agent
openai_api_key_social_media = os.getenv("OPENAI_API_MY_KEY")

# For Blog Review Agent (Agent 7)
openai_api_key_review = os.getenv("OPENAI_API_MY_KEY")

# For Perplexity Agent (used with requests)
perplexity_api_key = os.getenv("PERPLEXITY_API_KEY")

open_router_api_key = os.getenv("OPENROUTER_API_KEY")  # For OpenRouter API

# Assistant IDs for beta threads agents
CONTENT_SELECTOR_ASSISTANT_ID = "asst_ljaOpZCFP3yCVXXAycRGPfrU"
BLOG_WRITER_ASSISTANT_ID      = "asst_Gz1i8zpI8EWHP0EMWf1I4WOH"

sensitive_data = {
    'https://dev.to': {
        'email': os.getenv("EMAIL"),
        'password': "outskill50@",
    },
}


# === Medium Output now fetched via Agent ===
async def get_medium_output() -> str:
    user_task = ('''go to https://dev.to/ and then search for AI Agents or RAG in the search bar and press enter and then print the title of first 10 blogs''')
    print("🚀 Running Medium Agent...")
    execution_result = await run_agent_with_retry(user_task, openai_api_key_medium)

    if execution_result:
        extraction_prompt = f"""
        You are an AI assistant analyzing the full result of a browser automation agent run.
        Given the full execution result below, extract and return only the **final output** of the agent — typically the extracted_content from the step where \"is_done\": true.
        Execution Result: {execution_result}
        Respond with just the final output content. No prefix text. No suffix text.
        """
        extraction_llm = ChatOpenAI(model="gpt-4o", openai_api_key=openai_api_key_extraction)
        response = extraction_llm.invoke(extraction_prompt)
        final_output = response.content.strip()
        return final_output
    else:
        return ""

async def run_agent_with_retry(task_plan: str, api_key: str, max_retries: int = 3):
    """
    Uses a dedicated API key to run the browser automation agent.
    Retries the task up to max_retries times if errors occur.
    """
    retries = 0
    async with async_playwright() as playwright:
        while retries < max_retries:
            try:
                llm_local = ChatOpenAI(model="gpt-4o", openai_api_key=openai_api_key_breakdown)
                agent = Agent(task=task_plan, llm=llm_local, browser=Browser,browser_session=browser_session)
                result = await agent.run()  # Capture the final output of the agent run
                return result
            except Exception as e:
                print(f"[Medium Agent Attempt {retries+1}] Task failed: {e}")
                retries += 1
        print(f"❌ Medium Agent failed after {max_retries} retries. Aborting.")
        return None

# === Blog Pipeline Functions ===
def content_selector(ideas, api_key) -> list:
    openai.api_key = api_key
    thread = openai.beta.threads.create()
    thread_id = thread.id
    print ("Here are the ideas raw", ideas)
    openai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=[{"type": "text", "text": "\n".join(ideas)}]
    )

    run = openai.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=CONTENT_SELECTOR_ASSISTANT_ID
    )

    while True:
        run_status = openai.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if run_status.status == "completed":
            break
        elif run_status.status == "failed":
            print("❌ Assistant run failed.")
            return []
        time.sleep(1)

    messages = openai.beta.threads.messages.list(thread_id=thread_id)
    output_list = []
    for msg in reversed(messages.data):
        if msg.role == "assistant":
            for content_block in msg.content:
                if hasattr(content_block, "text"):
                    output_list.append(content_block.text.value)
            break
    return output_list

def run_perplexity_agent(idea: str) -> str:
    print(f"🔍 Running Perplexity agent for idea: {idea}")
    url = "https://openrouter.ai/api/v1/chat/completions"
    
    payload = {
        "model": "perplexity/sonar-pro",
        "messages": [
            {"role": "system", "content": "Be precise and concise."},
            {"role": "user", "content": f"Provide in-depth research on the following topic: {idea}"}
        ],
        "temperature": 0.2,
        "max_tokens": 500,
        "top_p": 0.9,
        "presence_penalty": 0,
        "frequency_penalty": 1,
        "stream": False
    }

    headers = {
        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
        "Content-Type": "application/json",
    }

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        return response.json()['choices'][0]['message']['content']
    else:
        print("❌ Error from OpenRouter:", response.status_code, response.text)
        return ""

def blog_writer(research_data: str, api_key: str) -> str:
    openai.api_key = api_key
    thread = openai.beta.threads.create()
    thread_id = thread.id

    openai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=f"Write a complete blog based on this research:\n{research_data}"
    )

    run_obj = openai.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=BLOG_WRITER_ASSISTANT_ID
    )

    while True:
        run_status = openai.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_obj.id)
        if run_status.status == "completed":
            break
        elif run_status.status == "failed":
            return ""
        time.sleep(1)

    messages = openai.beta.threads.messages.list(thread_id=thread_id)
    blog_post = ""
    for msg in reversed(messages.data):
        if msg.role == "assistant":
            for content_block in msg.content:
                if hasattr(content_block, "text"):
                    blog_post += content_block.text.value
            break
    return blog_post

async def ghost_draft(blog_post: str) -> str:
    async with async_playwright():
        llm = ChatOpenAI(model="gpt-4o", openai_api_key=openai_api_key_extraction)
        user_task = f"go to https://dev.to/ and login with email and password then click on create post and paste the article below in proper structured format as given to you and post it and then close the tab" + blog_post
        agent = Agent(task=user_task, llm=llm,sensitive_data=sensitive_data,browser_session=browser_session1,use_vision=False)
        result = await agent.run()
        return result

# === Shared State ===
class BlogState(TypedDict):
    ideas: List[str]
    selected_ideas: List[str]
    current_idea: Optional[str]
    research_data: Optional[str]
    blog_post: Optional[str]
    review_results: Optional[Dict]
    corrected_blog_post: Optional[str]
    reddit_post: Optional[str]
    hackernews_post: Optional[str]
    completed_blogs: dict

# === Node Functions ===
def select_ideas(state: BlogState) -> BlogState:
    import re
    raw_selected = content_selector(state["ideas"], openai_api_key_content_selector)
    print("this is raw selected", raw_selected)

    # Combine all messages into one string (in case it's a list)
    combined_text = "\n".join(raw_selected) if isinstance(raw_selected, list) else str(raw_selected)

    # Clean markdown like ```python [ ... ] ```
    code_block_match = re.search(r"```(?:python)?\s*(\[.*?\])\s*```", combined_text, re.DOTALL)
    if code_block_match:
        list_text = code_block_match.group(1)
    else:
        bracket_match = re.search(r"(\[.*\])", combined_text, re.DOTALL)
        list_text = bracket_match.group(1) if bracket_match else combined_text

    try:
        parsed = ast.literal_eval(list_text)
        if not isinstance(parsed, list):
            parsed = [parsed]
    except Exception as e:
        print("❌ Parsing error:", e)
        parsed = [list_text]

    # Final cleaned list
    cleaned = [item.strip().strip('"').strip("'") for item in parsed if isinstance(item, str) and item.strip()]

    # ✅ FIXED: Keep ALL ideas in selected_ideas, not just the first one
    print(f"📝 Selected {len(cleaned)} ideas for processing")
    
    return {
        **state,
        "selected_ideas": cleaned,  # ✅ Keep all ideas here
        "ideas": [],  # Clear original ideas since they're now in selected_ideas
        "completed_blogs": {},
    }

def pick_next_idea(state: BlogState) -> BlogState:
    print(f"📋 Current selected_ideas count: {len(state['selected_ideas'])}")
    
    if state["selected_ideas"]:
        # ✅ Make a copy to avoid modifying the original list
        remaining_ideas = state["selected_ideas"].copy()
        next_idea = remaining_ideas.pop(0)
        
        print(f"📌 Picking next idea: {next_idea}")
        print(f"📋 Remaining ideas: {len(remaining_ideas)}")
        
        return {
            **state, 
            "current_idea": next_idea,
            "selected_ideas": remaining_ideas  # ✅ Update with remaining ideas
        }
    else:
        print("❌ No more ideas to process")
        return {**state, "current_idea": None}

def research_agent(state: BlogState) -> BlogState:
    if not state["current_idea"]:
        print("❌ No current idea to research")
        return state
        
    print(f"🔍 Researching: {state['current_idea']}")
    research = run_perplexity_agent(state["current_idea"])
    
    if research:
        print(f"✅ Research completed for: {state['current_idea']}")
        print(f"📊 Research length: {len(research)} characters")
    else:
        print(f"❌ Research failed for: {state['current_idea']}")
    
    return {**state, "research_data": research}

def blog_writer_agent(state: BlogState) -> BlogState:
    if not state["research_data"]:
        print("❌ No research data available for blog writing")
        return state
        
    print(f"✍️ Writing blog for: {state['current_idea']}")
    blog = blog_writer(state["research_data"], openai_api_key_blog)
    
    if blog:
        print(f"✅ Blog written for: {state['current_idea']}")
        print(f"📄 Blog length: {len(blog)} characters")
    else:
        print(f"❌ Blog writing failed for: {state['current_idea']}")
    
    return {**state, "blog_post": blog}

def blog_review_agent(state: BlogState) -> BlogState:
    """
    Agent 7: Blog Review Agent - comprehensive review for errors and quality
    """
    if not state["blog_post"]:
        print("❌ No blog post available for review")
        return state
        
    blog_content = state["blog_post"]
    print(f"📝 Reviewing blog for: {state['current_idea']}")
    
    # Comprehensive review prompt
    review_prompt = f"""
    You are an expert blog reviewer and editor. Please conduct a comprehensive review of the following blog post and provide detailed feedback on:

    1. GRAMMAR & SPELLING:
       - Check for grammatical errors, typos, and spelling mistakes
       - Identify awkward phrasing or unclear sentences
       
    2. TECHNICAL ACCURACY:
       - Verify technical claims and statements
       - Check for outdated information or incorrect technical details
       - Validate any code snippets for syntax and best practices
       
    3. CONTENT STRUCTURE & FLOW:
       - Assess logical flow and organization
       - Check if introduction, body, and conclusion are well-connected
       - Evaluate paragraph transitions and coherence
       
    4. FACTUAL CONSISTENCY:
       - Identify any contradictory statements
       - Check for unsupported claims that need citations
       - Verify consistency of facts throughout the post
       
    5. WRITING STYLE & TONE:
       - Assess if tone is appropriate for target audience
       - Check for consistency in writing style
       - Evaluate readability and engagement level
       
    6. CODE VALIDATION (if applicable):
       - Check syntax and functionality of code snippets
       - Verify best practices and modern approaches
       - Ensure code examples are complete and runnable

    Please provide your response in the following JSON format:
    {{
        "overall_score": "score out of 10",
        "grammar_issues": ["list of grammar/spelling issues found"],
        "technical_issues": ["list of technical accuracy problems"],
        "structure_issues": ["list of structure and flow problems"],
        "factual_issues": ["list of factual inconsistencies"],
        "style_issues": ["list of style and tone problems"],
        "code_issues": ["list of code-related issues"],
        "suggestions": ["list of improvement suggestions"],
        "corrected_version": "provide a corrected version of the blog post with all issues fixed",
        "needs_major_revision": "true/false"
    }}

    Blog post to review:
    {blog_content}
    """
    
    try:
        review_llm = ChatOpenAI(model="gpt-4o", openai_api_key=openai_api_key_review)
        review_response = review_llm.invoke(review_prompt)
        review_text = review_response.content.strip()
        
        # Try to parse JSON response
        import json
        try:
            review_results = json.loads(review_text)
        except json.JSONDecodeError:
            # If JSON parsing fails, create a structured response
            review_results = {
                "overall_score": "Unable to parse",
                "grammar_issues": [],
                "technical_issues": [],
                "structure_issues": [],
                "factual_issues": [],
                "style_issues": [],
                "code_issues": [],
                "suggestions": ["Review completed but response format was invalid"],
                "corrected_version": blog_content,
                "needs_major_revision": "false",
                "raw_review": review_text
            }
        
        # Use corrected version if available, otherwise use original
        corrected_blog = review_results.get("corrected_version", blog_content)
        
        print(f"📝 Blog Review completed - Overall Score: {review_results.get('overall_score', 'N/A')}")
        print(f"🔍 Issues found: {len(review_results.get('grammar_issues', []))} grammar, {len(review_results.get('technical_issues', []))} technical")
        
        return {
            **state,
            "review_results": review_results,
            "corrected_blog_post": corrected_blog
        }
        
    except Exception as e:
        print(f"❌ Blog Review Agent failed: {e}")
        return {
            **state,
            "review_results": {
                "overall_score": "Error",
                "error": str(e),
                "suggestions": ["Review failed due to technical error"],
                "corrected_version": blog_content,
                "needs_major_revision": "false"
            },
            "corrected_blog_post": blog_content
        }

def social_media_agent(state: BlogState) -> BlogState:
    """
    Agent 5: Social Media Agent - converts blog content into Reddit and Hacker News posts
    """
    # Use corrected blog post if available, otherwise use original
    blog_content = state.get("corrected_blog_post") or state["blog_post"]
    
    if not blog_content:
        print("❌ No blog content available for social media conversion")
        return state
    
    print(f"📱 Creating social media posts for: {state['current_idea']}")
    
    # Generate Reddit post
    reddit_prompt = f"""
    Convert the following blog post into an engaging Reddit post format. 
    Create a catchy title and a brief engaging description that would work well on tech subreddits like r/programming, r/MachineLearning, or r/technology.
    
    Format your response as:
    TITLE: [Reddit post title]
    DESCRIPTION: [Brief engaging description]
    
    Blog content:
    {blog_content}
    """
    
    # Generate Hacker News post
    hackernews_prompt = f"""
    Convert the following blog post into a Hacker News post format.
    Create a concise, informative title and a brief summary that would appeal to the HN community.
    
    Format your response as:
    TITLE: [HN post title]
    SUMMARY: [Brief technical summary]
    
    Blog content:
    {blog_content}
    """
    
    try:
        # Generate Reddit post
        reddit_llm = ChatOpenAI(model="gpt-4o", openai_api_key=openai_api_key_social_media)
        reddit_response = reddit_llm.invoke(reddit_prompt)
        reddit_post = reddit_response.content.strip()
        
        # Generate Hacker News post
        hn_llm = ChatOpenAI(model="gpt-4o", openai_api_key=openai_api_key_social_media)
        hn_response = hn_llm.invoke(hackernews_prompt)
        hackernews_post = hn_response.content.strip()
        
        print(f"📱 Generated Reddit post: {reddit_post[:100]}...")
        print(f"🔶 Generated Hacker News post: {hackernews_post[:100]}...")
        
        return {
            **state, 
            "reddit_post": reddit_post,
            "hackernews_post": hackernews_post
        }
    except Exception as e:
        print(f"❌ Social Media Agent failed: {e}")
        return {
            **state,
            "reddit_post": "Failed to generate Reddit post",
            "hackernews_post": "Failed to generate Hacker News post"
        }

async def ghost_drafter_agent(state: BlogState) -> BlogState:
    # Use corrected blog post for publishing
    blog_to_publish = state.get("corrected_blog_post") or state["blog_post"]
    
    if not blog_to_publish:
        print("❌ No blog content available for publishing")
        return state
    
    print(f"🚀 Publishing blog for: {state['current_idea']}")
    
    try:
        await ghost_draft(blog_to_publish)
        print(f"✅ Blog published successfully for: {state['current_idea']}")
    except Exception as e:
        print(f"❌ Publishing failed for {state['current_idea']}: {e}")
    
    # Store completed blog details
    updated_completed = {
        **state["completed_blogs"],
        state["current_idea"]: {
            "original_blog_post": state["blog_post"],
            "corrected_blog_post": state.get("corrected_blog_post"),
            "review_results": state.get("review_results"),
            "reddit_post": state["reddit_post"],
            "hackernews_post": state["hackernews_post"]
        }
    }
    
    # Clear current processing data for next iteration
    return {
        **state,
        "completed_blogs": updated_completed,
        "blog_post": None,
        "review_results": None,
        "corrected_blog_post": None,
        "current_idea": None,
        "research_data": None,
        "reddit_post": None,
        "hackernews_post": None
    }

def has_more_ideas(state: BlogState) -> str:
    remaining_count = len(state["selected_ideas"])
    print(f"🔄 Checking for more ideas... Remaining: {remaining_count}")
    
    if remaining_count > 0:
        print("➡️ Moving to next idea")
        return "pick_next"
    else:
        print("🏁 All ideas processed. Ending workflow.")
        return END

# === LangGraph Definition ===
workflow = StateGraph(BlogState)
workflow.add_node("select_ideas", select_ideas)
workflow.add_node("pick_next", pick_next_idea)
workflow.add_node("research", research_agent)
workflow.add_node("write", blog_writer_agent)
workflow.add_node("review", blog_review_agent)
workflow.add_node("social_media", social_media_agent)
workflow.add_node("draft", ghost_drafter_agent)

# Set up the workflow flow
workflow.set_entry_point("select_ideas")
workflow.add_edge("select_ideas", "pick_next")
workflow.add_edge("pick_next", "research")
workflow.add_edge("research", "write")
workflow.add_edge("write", "review")
workflow.add_edge("review", "social_media")
workflow.add_edge("social_media", "draft")

# ✅ FIXED: Proper conditional edge mapping
workflow.add_conditional_edges("draft", has_more_ideas, {
    "pick_next": "pick_next", 
    END: END
})

app = workflow.compile()

# === Execute ===
if __name__ == "__main__":
    import asyncio
    
    print("🚀 Starting Blog Automation System...")
    
    # Get initial ideas from Medium/dev.to
    medium_raw = asyncio.run(get_medium_output())
    if not medium_raw:
        print("❌ Failed to fetch Medium output. Exiting.")
        exit()

    # Parse initial ideas
    initial_ideas = [idea.strip() for idea in medium_raw.strip().split("\n") if idea.strip()]
    print(f"📋 Found {len(initial_ideas)} initial ideas")

    initial_state: BlogState = {
        "ideas": initial_ideas,
        "selected_ideas": [],
        "current_idea": None,
        "research_data": None,
        "blog_post": None,
        "review_results": None,
        "corrected_blog_post": None,
        "reddit_post": None,
        "hackernews_post": None,
        "completed_blogs": {}
    }
    
    print("🚀 Launching LangGraph Blog Orchestrator...")
    
    try:
        final_state = app.invoke(initial_state)
        print(f"🎉 Workflow completed! Processed {len(final_state['completed_blogs'])} blogs.")
        
        # Print summary
        for idea, details in final_state['completed_blogs'].items():
            print(f"✅ Completed: {idea}")
            
    except Exception as e:
        print(f"❌ Workflow failed: {e}")
        import traceback
        traceback.print_exc()