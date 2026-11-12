"""Garde-fou de véracité : un document généré ne peut contenir ni outil ni chiffre inventé.

Contrôle fait par le code, pas par le LLM :
- tout terme technique connu (outil, langage, plateforme, méthode) présent dans le texte produit
  doit figurer dans les documents source (CV, et LM de référence pour la lettre) ;
- tout nombre du texte produit doit figurer dans les sources (ou dans l'offre, pour la lettre :
  « vos 20 agences » est un fait de l'offre, pas une affirmation sur le candidat) ;
- chaque compétence affichée doit citer son libellé d'origine, retrouvé dans le CV.
"""

import re

from rapidfuzz import fuzz

from app.collecte.normalisation import sans_accents

# Vocabulaire contrôlé : si l'un de ces termes apparaît dans un document généré, il doit exister
# dans le CV. Liste volontairement large (data, BI, cloud, dev, méthodes).
TERMES = """
sql python r scala java javascript typescript go rust c++ c# bash shell vba sas spss matlab julia
pandas numpy polars pyspark spark hadoop hive kafka flink airflow dagster prefect luigi nifi
dbt snowflake bigquery redshift databricks synapse fabric "azure data factory" "data factory" ssis ssas ssrs
"microsoft fabric" onelake "delta lake" iceberg hudi parquet avro
postgresql postgres mysql oracle "sql server" mongodb cassandra redis elasticsearch neo4j duckdb sqlite teradata
"power bi" tableau looker "looker studio" qlik qlikview qliksense metabase superset "data studio" excel
dax "power query" "power automate" "power apps" "tabular editor" "measure killer" mdx
aws azure gcp "google cloud" s3 lambda ec2 glue athena emr kinesis "cloud functions" "cloud run" dataflow dataproc
docker kubernetes terraform ansible jenkins "github actions" "gitlab ci" "azure devops" "ci/cd" git github gitlab
mlflow "scikit-learn" sklearn tensorflow pytorch keras xgboost lightgbm "machine learning" "deep learning" nlp llm
"data mesh" "data vault" kimball inmon "star schema" "modèle en étoile" "architecture médaillon" medallion
agile scrum kanban safe jira confluence notion
sap salesforce hubspot "google analytics" ga4 matomo segment amplitude mixpanel
fastapi flask django streamlit dash react
""".strip()
_TERMES = [t.strip('"') for t in re.findall(r'"[^"]+"|\S+', TERMES)]
_NOMBRE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w])")


def norm(texte: str) -> str:
    t = sans_accents(texte or "").lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9+#/.' -]+", " ", t)
    return " " + re.sub(r"\s+", " ", t) + " "


def _contient(texte_norm: str, terme: str) -> bool:
    t = norm(terme).strip()
    return re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", texte_norm) is not None


def termes_inventes(produit: str, sources: str) -> list[str]:
    p, s = norm(produit), norm(sources)
    return sorted({t for t in _TERMES if _contient(p, t) and not _contient(s, t)})


def nombres_inventes(produit: str, sources: str) -> list[str]:
    connus = {n.replace(",", ".") for n in _NOMBRE.findall(sources or "")}
    return sorted({n for n in _NOMBRE.findall(produit or "") if n.replace(",", ".") not in connus})


def libelle_dans_cv(source: str, texte_cv: str) -> bool:
    """La compétence citée (« source ») existe-t-elle dans le CV ? (tolère accents et casse)"""
    s, c = norm(source).strip(), norm(texte_cv)
    return len(s) >= 2 and (f" {s} " in c or s in c or fuzz.partial_ratio(s, c) >= 92)


def controler(produit: str, sources: str, sources_chiffres: str | None = None) -> dict:
    termes = termes_inventes(produit, sources)
    nombres = nombres_inventes(
        produit, sources_chiffres if sources_chiffres is not None else sources
    )
    return {
        "termes_inventes": termes,
        "nombres_inventes": nombres,
        "ok": not termes and not nombres,
    }
