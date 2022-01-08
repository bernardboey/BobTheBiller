import collections
import json
import os

from typing import Tuple, Optional, DefaultDict

from bson import json_util
import pymongo
from telegram.ext import BasePersistence
from telegram.ext.utils.types import CDCData, BD, CD, UD, ConversationDict
from telegram.utils.helpers import decode_user_chat_data_from_json

USERNAME = os.environ.get("MONGODB_USERNAME")
PASSWORD = os.environ.get("MONGODB_PASSWORD")


class MongoDB:
    def __init__(self):
        client = pymongo.MongoClient(
            f"mongodb+srv://{USERNAME}:{PASSWORD}@bobthebiller.3clgh.mongodb.net/BobTheBiller?retryWrites=true&w=majority")
        db = client.BobTheBiller
        self.collection: pymongo.collection.Collection = db.chat_data

    def insert(self, chat_id, data):
        self.collection.replace_one({"chat_id": chat_id}, {"chat_id": chat_id, "data": data}, upsert=True)

    def find(self):
        return self.collection.find()


def convert_str_keys_to_int(d):
    for key, value in list(d.items()):
        try:
            new_key = int(key)
            del d[key]
            d[new_key] = value
        except ValueError:
            pass
        if isinstance(value, dict):
            convert_str_keys_to_int(value)


class MongoPersistence(BasePersistence):
    def __init__(self):
        super().__init__(store_user_data=False, store_chat_data=True, store_bot_data=False, store_callback_data=False)
        self.db = MongoDB()
        self.chat_data = collections.defaultdict(dict)

    def get_user_data(self) -> DefaultDict[int, UD]:
        return collections.defaultdict(dict)

    def get_chat_data(self) -> DefaultDict[int, CD]:
        data = {}
        for doc in self.db.find():
            data.update(doc["data"])
        chat_data_json = json_util.dumps(data)
        self.chat_data = decode_user_chat_data_from_json(chat_data_json)
        convert_str_keys_to_int(self.chat_data)
        return self.chat_data

    def get_bot_data(self) -> BD:
        return {}

    def get_callback_data(self) -> Optional[CDCData]:
        pass

    def get_conversations(self, name: str) -> ConversationDict:
        pass

    def update_conversation(self, name: str, key: Tuple[int, ...], new_state: Optional[object]) -> None:
        pass

    def update_user_data(self, user_id: int, data: UD) -> None:
        pass

    def update_chat_data(self, chat_id: int, data: CD) -> None:
        if self.chat_data.get(chat_id) == data:
            return
        self.chat_data[chat_id] = data
        chat_data_json = json_util.loads(json.dumps(self.chat_data, default=json_util.default))
        self.db.insert(chat_id, chat_data_json)

    def update_bot_data(self, data: BD) -> None:
        pass

    def update_callback_data(self, data: CDCData) -> None:
        pass
