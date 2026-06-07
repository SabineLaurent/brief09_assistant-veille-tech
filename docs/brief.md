# Contexte du projet

Nauda Palisse est un SaaS de dev-tooling. L'équipe produit (devs + PM + DevRel) consacre une demi-journée par semaine à la veille techno, dispersée entre Hacker News, Twitter, RSS de changelogs et newsletters. La CTO veut rapatrier cette veille dans un assistant interne capable de répondre à des questions du type :

« Quelles tendances reviennent cette semaine ? »
« Quels outils sont les plus cités sur le sujet X ? »
« Quels changements côté Vercel / OpenAI / Next.js cette semaine ? »

Il n'existe aucun pipeline d'ingestion aujourd'hui. La stack RAG (FastAPI + Chroma + LangChain → Azure AI Inference avec Kimi-K2.6) et le frontend Next.js sont déjà déployés et fonctionnels sur une base vide (plus ou moins). Vous prenez en charge toute la collecte (ingestion API, scraping de sources tech, nettoyage HTML→Markdown, chunking, indexation) et l'injection runtime des news fraîches au moment du chat.
