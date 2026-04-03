import chromadb
from langchain_community.vectorstores import Chroma
from langchain_ollama import OllamaEmbeddings

class PreferenceStore:
    def __init__(self):
        # Persistent storage in the /data folder
        self.client = chromadb.PersistentClient(path="./data")
        self.collection_name = "user_prefs"
        
        # Ensure collection exists up front
        self.client.get_or_create_collection(name=self.collection_name)
        
        self.embeddings = OllamaEmbeddings(model="llama3.2:1b")
        self.vector_store = Chroma(
            client=self.client,
            collection_name=self.collection_name,
            embedding_function=self.embeddings
        )

    def upsert_preference(self, user_id, text, lat=None, lng=None):
        # Store preference as a vector utilizing upsert
        collection = self.client.get_collection(name=self.collection_name)
        metadata = {}
        if lat is not None and lng is not None:
            metadata = {"lat": float(lat), "lng": float(lng)}
            
        collection.upsert(
            documents=[text],
            ids=[user_id],
            metadatas=[metadata] if metadata else None
        )

    def get_user_location(self, user_id):
        collection = self.client.get_collection(name=self.collection_name)
        result = collection.get(ids=[user_id], include=["metadatas"])
        if result and result.get('metadatas') and len(result['metadatas']) > 0:
            meta = result['metadatas'][0]
            if meta and 'lat' in meta and 'lng' in meta:
                return meta['lat'], meta['lng']
        return None, None

    def get_retriever(self, k=1):
        # Find preferences that match a specific "vibe" or query
        return self.vector_store.as_retriever(search_kwargs={"k": k})