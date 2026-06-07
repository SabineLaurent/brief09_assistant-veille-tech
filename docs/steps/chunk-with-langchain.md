Ton chunk() manuel :
- Découpe sur les fins de phrase ([.!?])
- Overlap manuel (un peu fragile)
- ~20 lignes à maintenir

RecursiveCharacterTextSplitter de LangChain :
- Essaie par ordre : \n\n → \n → .  →   → caractère par caractère
- Gère le chevauchement nativement avec chunk_overlap
- Testé sur des millions de cas réels, 2 lignes de code

from langchain.text_splitter import RecursiveCharacterTextSplitter

def chunk(text: str, max_chars: int = 1200) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=max_chars, chunk_overlap=100)
    return splitter.split_text(text)

Mon avis : oui, remplace par LangChain. C'est plus robuste, plus lisible, et ça respecte KISS. Le seul inconvénient est que tu perds le paramètre overlap_sentences (qui disparaît au profit d'overlap en caractères) — mais c'est un bon échange.

---
