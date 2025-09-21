#!/usr/bin/env python3
"""
Test real agent functionality without UI to verify API compliance
"""

from agent_chat_app import new_agent_conversation, add_user_message_and_run_agent, get_conversation_history
import json

def test_real_agent():
    print("🚀 Testing Real Agent Functionality")
    print("=" * 40)
    
    try:
        # Create new conversation
        thread_id = new_agent_conversation('openai/gpt-5-nano', 0.7)
        print(f"✅ Created agent thread: {thread_id}")
        
        # Test 1: List files
        print(f"\n📝 Test 1: Asking agent to list files...")
        iterations = add_user_message_and_run_agent(
            thread_id, 
            "Please list all files in the notes directory", 
            'openai/gpt-5-nano', 
            0.7
        )
        print(f"✅ Agent completed in {iterations} iterations")
        
        # Test 2: Read a file
        print(f"\n📖 Test 2: Asking agent to read a file...")
        iterations = add_user_message_and_run_agent(
            thread_id, 
            "Please read the content of test.txt", 
            'openai/gpt-5-nano', 
            0.7
        )
        print(f"✅ Agent completed in {iterations} iterations")
        
        # Test 3: Create a file
        print(f"\n✏️ Test 3: Asking agent to create a file...")
        iterations = add_user_message_and_run_agent(
            thread_id, 
            "Please create a file called 'agent_test.txt' with the content 'This file was created by the agent successfully!'", 
            'openai/gpt-5-nano', 
            0.7
        )
        print(f"✅ Agent completed in {iterations} iterations")
        
        # Show final conversation
        print(f"\n💬 Final conversation history:")
        history = get_conversation_history(thread_id)
        for i, msg in enumerate(history):
            role = msg['role']
            content = msg['content'][:150] + "..." if len(msg['content']) > 150 else msg['content']
            print(f"{i+1}. {role.upper()}: {content}")
        
        print(f"\n🎉 Real agent test completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Error during real agent test: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_real_agent()
    if success:
        print("\n✅ Agent is working correctly! No OpenAI API format errors.")
    else:
        print("\n❌ Agent test failed.")