from langchain_core.prompts import ChatPromptTemplate
from src.logic.database import PreferenceStore
from src.services.llm_provider import LlamaProvider

class MeetingScheduler:
    def __init__(self):
        self.model = LlamaProvider.get_model()
        self.preference_store = PreferenceStore()

    def negotiate_plan(self, group_chat_summary, available_venues):
        """
        Takes the messy chat history and the list of venues to pick a winner.
        """
        template = """
        You are a social coordinator agent for Team 10.
        Group Chat Context: {chat_history}
        Retrieved User Personas: {personas}
        Potential Venues: {venues}
        
        Task: Based on the group's vibe, their personas, and the venue ratings, pick the BEST 
        meeting spot and time. Be concise and helpful.
        
        IMPORTANT: If 'Potential Venues' is empty or invalid ('[]'), assume it is physically impossible to meet 
        at the requested spot. Draft a helpful response telling the user why, and ask them to renegotiate 
        the location, expand the search criteria, or suggest a new time.
        """
        
        prompt = ChatPromptTemplate.from_template(template)
        chain = prompt | self.model
        
        # Retrieve context from vector store using LangChain Retriever
        retriever = self.preference_store.get_retriever(k=2) # We'll just retrieve Top 2 for context
        try:
            docs = retriever.invoke(group_chat_summary)
            personas_context = "\n".join([doc.page_content for doc in docs]) if docs else "No personas found in memory."
        except Exception as e:
            # gracefully fallback if collection error
            personas_context = f"No personas retrieved (error: {e})."

        # Run the AI logic
        response = chain.invoke({
            "chat_history": group_chat_summary,
            "personas": personas_context,
            "venues": available_venues
        })
        
        return response

# Example Test:
# agent = MeetingScheduler()
# print(agent.negotiate_plan("John wants Friday, Alfredo hates fish", "Bar A (4.5 stars), Sushi B (3.0 stars)"))