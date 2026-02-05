import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL_NAME = "BAAI/bge-reranker-base"

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)

model.eval()


def rerank(query: str, documents: list, top_k: int = 5):
    """
    documents: list of dicts containing 'text'
    """

    if not documents:
        return []

    pairs = [[query, doc["text"]] for doc in documents]

    with torch.no_grad():
        inputs = tokenizer(
            pairs,
            padding=True,
            truncation=True,
            return_tensors="pt"
        )
        scores = model(**inputs).logits.squeeze()

    # Attach scores
    for doc, score in zip(documents, scores):
        doc["rerank_score"] = float(score)

    # Sort by rerank score
    ranked = sorted(
        documents,
        key=lambda x: x["rerank_score"],
        reverse=True
    )

    return ranked[:top_k]
