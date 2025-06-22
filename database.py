import os
from pymongo import MongoClient, UpdateOne
from bson.objectid import ObjectId

# --- MONGODB SETUP ---
MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    raise Exception("MONGO_URI environment variable not set!")

client = MongoClient(MONGO_URI)
db = client.get_database("CompatibilityBotDB") 
phones_collection = db["phones"]
groups_collection = db["compatibility_groups"]

phones_collection.create_index("search_key", unique=True)
groups_collection.create_index("type")


# --- DATABASE FUNCTIONS ---

def find_phone(model_name: str) -> dict or None:
    """Finds a phone by its model name, using a case-insensitive search key."""
    search_key = model_name.lower()
    return phones_collection.find_one({"search_key": search_key})

def get_all_phones() -> list:
    """Retrieves all unique phone model names."""
    all_docs = phones_collection.find({}, {"_id": 1})
    return sorted([doc['_id'] for doc in all_docs])

def delete_phone(model_name: str) -> bool:
    """Deletes a phone and its references from any compatibility groups."""
    phone_doc = find_phone(model_name)
    if not phone_doc:
        return False

    display_group_id = phone_doc.get('display_group_id')
    glass_group_id = phone_doc.get('glass_group_id')

    if display_group_id:
        groups_collection.update_one(
            {"_id": display_group_id},
            {"$pull": {"models": model_name}}
        )
    if glass_group_id:
        groups_collection.update_one(
            {"_id": glass_group_id},
            {"$pull": {"models": model_name}}
        )

    result = phones_collection.delete_one({"search_key": model_name.lower()})
    return result.deleted_count > 0

def get_compatible_models(model_name: str, part_type: str) -> list:
    """Gets all models compatible with the given model for a specific part."""
    phone_doc = find_phone(model_name)
    if not phone_doc:
        return []

    group_id_key = f"{part_type}_group_id"
    group_id = phone_doc.get(group_id_key)

    if not group_id:
        return [model_name]

    group_doc = groups_collection.find_one({"_id": group_id})
    if group_doc:
        models = group_doc.get('models', [])
        return sorted([m for m in models if m.lower() != model_name.lower()])

    return []

def link_parts(model_names: list, part_type: str):
    """
    Links a list of models to the same compatibility group for a given part.
    If they belong to different groups, it merges them into one.
    """
    group_id_key = f"{part_type}_group_id"
    
    model_names_lower = [m.lower() for m in model_names]
    query = {"search_key": {"$in": model_names_lower}}
    existing_phones = list(phones_collection.find(query))
    
    existing_group_ids = {phone[group_id_key] for phone in existing_phones if group_id_key in phone}
    
    all_models_in_groups = set(model_names)

    if existing_group_ids:
        group_docs = list(groups_collection.find({"_id": {"$in": list(existing_group_ids)}}))
        for group in group_docs:
            all_models_in_groups.update(group.get('models', []))

    final_model_list = sorted(list(all_models_in_groups), key=str.casefold)

    if existing_group_ids:
        primary_group_id = ObjectId(sorted([str(id) for id in existing_group_ids])[0])
        groups_to_delete = existing_group_ids - {primary_group_id}
    else:
        new_group = groups_collection.insert_one({'type': part_type, 'models': []})
        primary_group_id = new_group.inserted_id
        groups_to_delete = set()
        
    bulk_ops = []

    groups_collection.update_one(
        {"_id": primary_group_id},
        {"$set": {"models": final_model_list, "type": part_type}}
    )

    for model_name in final_model_list:
        op = UpdateOne(
            {"search_key": model_name.lower()},
            {
                "$set": {group_id_key: primary_group_id},
                "$setOnInsert": {"_id": model_name, "search_key": model_name.lower()}
            },
            upsert=True
        )
        bulk_ops.append(op)
    
    if bulk_ops:
        phones_collection.bulk_write(bulk_ops)

    if groups_to_delete:
        groups_collection.delete_many({"_id": {"$in": list(groups_to_delete)}})
