# Old: from langchain_community.llms import Ollama
# New:
from langchain_ollama import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate

class MeetingScheduler:
    def __init__(self):
        # Changed from llama3.1:8b to llama3.2:3b just for dev 
        self.model = OllamaLLM(model="llama3.2:1b")

    def negotiate_plan(self, group_chat_summary, available_venues):
        """
        Takes the messy chat history and the list of venues to pick a winner.
        """
        template = """
        You are a social coordinator agent for Team 10.
        Group Chat Context: {chat_history}
        Potential Venues: {venues}
        
        Task: Based on the group's vibe and the venue ratings, pick the BEST 
        meeting spot and time. Be concise and helpful.
        """
        
        prompt = ChatPromptTemplate.from_template(template)
        chain = prompt | self.model
        
        # Run the AI logic
        response = chain.invoke({
            "chat_history": group_chat_summary,
            "venues": available_venues
        })
        
        return response

# Example Test:
# agent = MeetingScheduler()
# print(agent.negotiate_plan("John wants Friday, Alfredo hates fish", "Bar A (4.5 stars), Sushi B (3.0 stars)"))