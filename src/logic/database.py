import chromadb

class PreferenceStore:
    def __init__(self):
        # Persistent storage in the /data folder
        self.client = chromadb.PersistentClient(path="./data")
        self.collection = self.client.get_or_create_collection(name="user_prefs")

    def add_preference(self, user_id, text):
        # Store preference as a vector
        self.collection.add(
            documents=[text],
            ids=[user_id]
        )

    def get_match(self, query):
        # Find preferences that match a specific "vibe" or query
        return self.collection.query(query_texts=[query], n_results=1)