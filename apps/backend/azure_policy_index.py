"""
Azure Policy Index - Azure AI Search Integration for Literal Text Retrieval

This module provides Azure-native integration for the policy chunker:
- Creates and manages Azure AI Search index with optimized schema
- Uploads chunks with vector embeddings
- Provides hybrid search (vector + keyword) for retrieval
- Supports differential sync via content hashing

Usage:
    from azure_policy_index import PolicySearchIndex

    index = PolicySearchIndex()
    index.create_index()  # One-time setup
    index.upload_chunks(chunks)  # Batch upload
    results = index.search("chaperone duties")  # Hybrid search
"""

import os
import hashlib
import logging
import time
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(env_path)

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Configure logging
logger = logging.getLogger(__name__)
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    VectorSearch,
    VectorSearchProfile,
    HnswAlgorithmConfiguration,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SynonymMap,
)
from azure.search.documents.models import VectorizableTextQuery
from openai import AzureOpenAI

# Import our chunker
from preprocessing.chunker import PolicyChunk


# Configuration from environment
SEARCH_ENDPOINT = os.environ.get("SEARCH_ENDPOINT", "https://policychataisearch.search.windows.net")
SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY")
AOAI_ENDPOINT = os.environ.get("AOAI_ENDPOINT")
AOAI_API_KEY = os.environ.get("AOAI_API")
AOAI_EMBEDDING_DEPLOYMENT = os.environ.get("AOAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")

# Index configuration
INDEX_NAME = "rush-policies"
EMBEDDING_DIMENSIONS = 3072  # text-embedding-3-large
SYNONYM_MAP_NAME = "rush-policy-synonyms"

# Comprehensive healthcare synonyms for Azure AI Search
# Format: comma-separated terms on each line are treated as equivalent (bidirectional)
# These complement query-time expansion by handling cases where documents use different terms
SYNONYMS = """
# ============================================================================
# RUSH INSTITUTION TERMS (Critical for multi-entity searches)
# ============================================================================
RUMC, Rush University Medical Center, Rush Medical Center, Rush Hospital, the hospital
RUMG, Rush University Medical Group, Rush Medical Group, physician group, medical group
ROPH, Rush Oak Park Hospital, Oak Park Hospital, Rush Oak Park
RCMC, Rush Copley Medical Center, Copley Medical Center, Rush Copley, Copley hospital
RCH, Rush Childrens Hospital, Rush Children's Hospital, pediatric hospital
ROPPG, Rush Oak Park Physicians Group, Oak Park Physicians Group
RCMG, Rush Copley Medical Group, Copley Medical Group
RU, Rush University, the university
RAB, Rush Ambulatory Building, Rubschlager Building, ambulatory building, outpatient building

# ============================================================================
# EMERGENCY & HOSPITAL CODES (Life-critical - must match perfectly)
# ============================================================================
code blue, cardiac arrest, cardiopulmonary arrest, resuscitation, medical emergency
code pink, missing infant, infant abduction, child abduction, missing baby
code gold, patient elopement, missing patient, patient wandering, AWOL
code gray, combative patient, violent patient, aggressive behavior, behavioral emergency
code silver, active shooter, person with weapon, armed individual, weapon threat
code orange, hazmat, hazardous materials, chemical spill, decontamination
code red, fire emergency, fire alarm, smoke, evacuation fire
code stroke, stroke alert, brain attack, CVA, cerebrovascular accident
code STEMI, heart attack, myocardial infarction, STEMI alert
code sepsis, sepsis alert, severe infection, septic shock
code trauma, trauma alert, major injury, trauma activation
rapid response, RRT, rapid response team, medical emergency team, MET

# ============================================================================
# DEPARTMENTS & UNITS (High-frequency searches)
# ============================================================================
ED, ER, emergency department, emergency room, trauma center, emergency services
ICU, intensive care unit, critical care, CCU, MICU, SICU
NICU, neonatal intensive care unit, newborn ICU, neonatal ICU, special care nursery
PICU, pediatric intensive care unit, pediatric ICU, childrens ICU
OR, operating room, surgery suite, surgical suite, operating theater, perioperative
PACU, post anesthesia care unit, recovery room, post-op, post operative
L&D, LD, labor and delivery, maternity, birthing, obstetrics delivery
postpartum, mother baby, mother-baby, maternity ward
ambulatory, outpatient, clinic, outpatient clinic, ambulatory care
radiology, diagnostic imaging, imaging services, x-ray, CT, MRI, ultrasound
laboratory, lab, pathology, clinical lab, blood lab, diagnostic lab
pharmacy, medication services, pharmaceutical services, drug dispensing
respiratory care, respiratory therapy, RT, pulmonary services
food and nutrition, dietary, food services, nutrition services, FNS
environmental services, housekeeping, EVS, cleaning services, janitorial
patient access, registration, admissions, patient registration
revenue cycle, billing, patient accounting, charge capture, coding
infection prevention, infection control, epidemiology

# ============================================================================
# CLINICAL PROCEDURES & TREATMENTS
# ============================================================================
intubation, tube insertion, airway management, ETT placement, endotracheal intubation
catheterization, catheter insertion, foley catheter, urinary catheter, bladder catheter
central line placement, CVC insertion, central venous access, PICC placement
lumbar puncture, spinal tap, LP, CSF collection
blood transfusion, transfusion, blood administration, blood products, PRBC
dialysis, hemodialysis, CRRT, renal replacement therapy, kidney dialysis
ventilation, mechanical ventilation, ventilator support, respiratory support
sedation, conscious sedation, moderate sedation, procedural sedation
CPR, cardiopulmonary resuscitation, chest compressions, resuscitation, BLS
defibrillation, cardioversion, AED use, electrical cardioversion
phlebotomy, blood draw, venipuncture, blood collection
IV, intravenous, infusion, drip, IV therapy
restraints, physical restraints, patient restraints, limb restraints

# ============================================================================
# PATIENT SAFETY (Critical for compliance searches)
# ============================================================================
fall prevention, fall risk, fall precautions, falls protocol, patient falls
patient identification, patient ID, two patient identifiers, ID verification
hand hygiene, handwashing, hand washing, hand sanitizing
medication reconciliation, med rec, medication review, reconciling medications
medication error, drug error, dosing error, dispensing error, wrong medication
adverse event, adverse drug event, ADE, adverse reaction, medication reaction
sentinel event, serious reportable event, never event, SRE
near miss, close call, good catch, prevented error
time out, surgical time out, universal protocol, pre-procedure verification, safety pause
pressure injury, pressure ulcer, bedsore, decubitus ulcer, skin breakdown
latex, natural rubber latex, latex allergy, latex precautions, latex product, latex sensitivity, NRL

# ============================================================================
# COMPLIANCE & REGULATORY
# ============================================================================
HIPAA, privacy, patient privacy, PHI, protected health information, confidentiality
EMTALA, emergency treatment law, anti-dumping, medical screening, stabilization requirement
informed consent, consent, patient consent, surgical consent, procedure consent
advance directive, living will, healthcare proxy, DPOA, POLST, DNR
DNR, do not resuscitate, no code, allow natural death, AND, comfort care
compliance, regulatory compliance, policy compliance, accreditation
Joint Commission, TJC, JCAHO, accreditation, CMS certification
IRB, Institutional Review Board, research ethics, human subjects committee

# ============================================================================
# DOCUMENTATION & RECORDS
# ============================================================================
medical record, chart, patient record, health record, EHR, EMR
progress note, clinical note, physician note, provider note
discharge summary, discharge note, DC summary, hospital summary
verbal orders, telephone orders, phone orders, read back
CPOE, computerized physician order entry, order entry, order placement

# ============================================================================
# COMMUNICATION & HANDOFF (Fix for gen-004, gen-006)
# ============================================================================
SBAR, Situation Background Assessment Recommendation, handoff communication, patient handoff, handoff report
shift report, change of shift report, hand-off report, bedside report, nursing handoff, shift change report
hand-off, handoff, patient handoff, nursing handoff, care transition

# ============================================================================
# EQUIPMENT & DEVICES
# ============================================================================
ventilator, vent, breathing machine, respirator, mechanical ventilator
infusion pump, IV pump, smart pump, medication pump, PCA pump
monitor, cardiac monitor, bedside monitor, patient monitor, telemetry
defibrillator, defib, AED, automated external defibrillator
crash cart, code cart, emergency cart, resuscitation cart
pyxis, medication cabinet, ADC, automated dispensing cabinet, med dispenser
oxygen equipment, O2, nasal cannula, face mask, high flow, BiPAP, CPAP

# ============================================================================
# STAFF ROLES
# ============================================================================
physician, doctor, MD, DO, attending, hospitalist, provider
nurse, RN, registered nurse, staff nurse, bedside nurse
nurse practitioner, NP, APRN, advanced practice nurse, APN
physician assistant, PA, PA-C, physician associate
CNA, certified nursing assistant, nursing assistant, nurse aide, patient care tech, PCT
respiratory therapist, RT, respiratory care practitioner, RCP
pharmacist, PharmD, clinical pharmacist, staff pharmacist
charge nurse, shift supervisor, unit supervisor, nurse in charge

# ============================================================================
# CONDITIONS & DIAGNOSES (Common search terms)
# ============================================================================
heart attack, myocardial infarction, MI, STEMI, NSTEMI, acute coronary syndrome
stroke, CVA, cerebrovascular accident, brain attack, TIA
sepsis, septicemia, blood infection, systemic infection, septic shock
pneumonia, lung infection, respiratory infection, CAP, HAP, VAP
diabetes, DM, diabetes mellitus, hyperglycemia, blood sugar
hypertension, high blood pressure, HTN, elevated BP
DVT, deep vein thrombosis, blood clot, venous thrombosis
PE, pulmonary embolism, lung clot, pulmonary thromboembolism
UTI, urinary tract infection, bladder infection
CHF, congestive heart failure, heart failure, HF, fluid overload

# ============================================================================
# MEDICATIONS & DRUGS
# ============================================================================
antibiotic, antibacterial, anti-infective, antimicrobial
narcotic, opioid, controlled substance, pain medication
anticoagulant, blood thinner, warfarin, heparin, coumadin
insulin, diabetic medication, blood sugar medication
chemotherapy, chemo, antineoplastic, cancer treatment
controlled substance, scheduled medication, DEA controlled, C-II, CII
high alert medication, high risk medication, LASA, look alike sound alike

# ============================================================================
# INFECTION CONTROL
# ============================================================================
standard precautions, universal precautions, basic precautions
isolation, contact isolation, droplet isolation, airborne isolation, transmission precautions
sterile technique, aseptic technique, sterile procedure, surgical asepsis
PPE, personal protective equipment, gown, gloves, mask, face shield
N95, respirator, N95 mask, particulate respirator
bloodborne pathogen, BBP, blood exposure, needle stick, sharps injury
HAI, healthcare associated infection, hospital acquired infection, nosocomial infection
CLABSI, central line associated bloodstream infection, line infection
CAUTI, catheter associated UTI, catheter infection, foley infection
SSI, surgical site infection, wound infection, post-operative infection

# ============================================================================
# SOFTWARE SYSTEMS
# ============================================================================
Epic, electronic health record, EHR, EMR, electronic medical record
Pyxis, automated dispensing cabinet, ADC, medication cabinet
MyChart, patient portal, my chart, online portal
Workday, HR system, human resources system, payroll system
Kronos, timekeeping system, time clock, time tracking

# ============================================================================
# TIME & SCHEDULING
# ============================================================================
STAT, immediately, urgent, emergent, right away, priority
PRN, as needed, as necessary, when needed
NPO, nothing by mouth, fasting, no eating or drinking
on call, call coverage, night call, backup coverage

# ============================================================================
# OBSTETRICS & NEONATAL
# ============================================================================
cesarean, c-section, cesarean section, CS, surgical delivery
epidural, labor epidural, epidural anesthesia
fetal monitoring, FHR monitoring, electronic fetal monitoring, EFM
newborn, neonate, infant, baby, newborn infant
breastfeeding, nursing, lactation, breast milk
preterm, premature, preemie, premature infant
"""


@dataclass
class SearchResult:
    """A search result with chunk content and metadata."""
    content: str
    citation: str
    title: str
    section: str
    applies_to: str
    date_updated: str
    score: float
    reference_number: str = ""
    reranker_score: Optional[float] = None
    source_file: str = ""
    document_owner: str = ""
    date_approved: str = ""
    # Entity-specific booleans
    applies_to_rumc: bool = False
    applies_to_rumg: bool = False
    applies_to_rmg: bool = False
    applies_to_roph: bool = False
    applies_to_rcmc: bool = False
    applies_to_rch: bool = False
    applies_to_roppg: bool = False
    applies_to_rcmg: bool = False
    applies_to_ru: bool = False
    # Hierarchical fields
    chunk_level: str = "semantic"
    parent_chunk_id: Optional[str] = None
    chunk_index: int = 0
    # Enhanced metadata
    category: Optional[str] = None
    subcategory: Optional[str] = None
    regulatory_citations: Optional[str] = None
    related_policies: Optional[str] = None

    def format_for_rag(self) -> str:
        """Format result for RAG prompt context with full metadata."""
        # Extract reference from citation for cleaner display
        ref_part = "N/A"
        if '(' in self.citation and ')' in self.citation:
            try:
                ref_part = self.citation.split('(')[1].split(')')[0]
            except (IndexError, AttributeError):
                ref_part = "N/A"

        reference_display = self.reference_number or ref_part

        return f"""┌────────────────────────────────────────────────────────────┐
│ POLICY: {self.title}
│ Reference: {reference_display}
│ Section: {self.section}
│ Applies To: {self.applies_to}
│ Document Owner: {self.document_owner or 'N/A'}
│ Updated: {self.date_updated} | Approved: {self.date_approved or 'N/A'}
│ Source: {self.source_file}
└────────────────────────────────────────────────────────────┘

{self.content}
"""


class PolicySearchIndex:
    """
    Manages Azure AI Search index for policy chunks.

    Features:
    - Hybrid search (vector + keyword + semantic ranking)
    - Optimized schema for literal text retrieval
    - Batch upload with automatic embedding generation
    - Content hash tracking for differential sync
    """

    def __init__(
        self,
        index_name: str = INDEX_NAME,
        search_endpoint: str = SEARCH_ENDPOINT,
        search_api_key: str = SEARCH_API_KEY,
        aoai_endpoint: str = AOAI_ENDPOINT,
        aoai_api_key: str = AOAI_API_KEY,
        embedding_deployment: str = AOAI_EMBEDDING_DEPLOYMENT
    ):
        """
        Initialize search index client.

        Credential resolution order (logged with [CREDENTIAL] prefix):
        1. search_api_key parameter (explicit API key)
        2. SEARCH_API_KEY environment variable
        3. DefaultAzureCredential (managed identity / az cli)
        """
        self.index_name = index_name
        self.embedding_deployment = embedding_deployment

        # Track credential resolution for debugging
        credential_source = "unknown"
        endpoint = None
        credential = None

        # Priority 1: Explicit parameter (API key)
        if search_api_key:
            endpoint = search_endpoint
            credential = AzureKeyCredential(search_api_key)
            credential_source = "parameter (API key)"

        # Priority 2: Environment variable (API key)
        elif SEARCH_API_KEY:
            endpoint = search_endpoint or SEARCH_ENDPOINT
            credential = AzureKeyCredential(SEARCH_API_KEY)
            credential_source = "env SEARCH_API_KEY"

        # Priority 3: DefaultAzureCredential fallback (managed identity / az cli)
        else:
            endpoint = search_endpoint or SEARCH_ENDPOINT
            credential = DefaultAzureCredential()
            credential_source = "DefaultAzureCredential (no API key found)"

        # Ensure endpoint is set
        if not endpoint:
            endpoint = SEARCH_ENDPOINT

        # Log final credential decision for debugging
        logger.info(f"[CREDENTIAL] PolicySearchIndex using: {credential_source}")

        self.search_endpoint = endpoint
        self.credential = credential

        # Azure Search clients
        self.index_client = SearchIndexClient(
            endpoint=self.search_endpoint,
            credential=self.credential
        )
        self.search_client = SearchClient(
            endpoint=self.search_endpoint,
            index_name=index_name,
            credential=self.credential
        )

        # Azure OpenAI client for embeddings
        self.aoai_client = AzureOpenAI(
            azure_endpoint=aoai_endpoint,
            api_key=aoai_api_key,
            api_version="2024-06-01",
            timeout=15.0  # 15-second timeout for fast failure detection
        )

        # Store AOAI config for vectorizer
        self.aoai_endpoint = aoai_endpoint
        self.aoai_api_key = aoai_api_key

    def get_search_client(self) -> SearchClient:
        """Return the SearchClient instance for direct operations."""
        return self.search_client

    def create_synonym_map(self) -> None:
        """
        Create or update the synonym map for domain-specific terminology.
        
        Synonym maps enable Azure AI Search to find documents even when users
        use different terms than what's in the documents (e.g., "radiology" 
        finds "diagnostic services").
        """
        # Parse synonyms - filter out comments and empty lines
        synonym_rules = []
        for line in SYNONYMS.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                synonym_rules.append(line)
        
        synonyms_text = '\n'.join(synonym_rules)
        
        synonym_map = SynonymMap(
            name=SYNONYM_MAP_NAME,
            synonyms=synonyms_text
        )
        
        self.index_client.create_or_update_synonym_map(synonym_map)
        logger.info(f"Synonym map '{SYNONYM_MAP_NAME}' created/updated with {len(synonym_rules)} rules")

    def create_index(self) -> None:
        """
        Create or update the search index with optimized schema.

        Schema design for literal retrieval:
        - content: Full searchable text (keyword search)
        - content_vector: Dense vector for semantic search
        - citation: Pre-formatted citation string
        - All metadata fields filterable for faceted search
        """
        fields = [
            # Key field
            SimpleField(
                name="id",
                type=SearchFieldDataType.String,
                key=True,
                filterable=True
            ),

            # Main content - searchable for keyword matching with synonym expansion
            SearchableField(
                name="content",
                type=SearchFieldDataType.String,
                analyzer_name="en.microsoft",
                synonym_map_names=[SYNONYM_MAP_NAME]
            ),

            # Vector field for semantic search
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=EMBEDDING_DIMENSIONS,
                vector_search_profile_name="default-profile"
            ),

            # Policy title - searchable and filterable
            SearchableField(
                name="title",
                type=SearchFieldDataType.String,
                filterable=True,
                facetable=True
            ),

            # Reference number - for exact lookups
            SimpleField(
                name="reference_number",
                type=SearchFieldDataType.String,
                filterable=True,
                facetable=True
            ),

            # Section info - searchable for section-specific queries
            SearchableField(
                name="section",
                type=SearchFieldDataType.String,
                filterable=True
            ),

            # Pre-formatted citation - just retrieve, no search
            SimpleField(
                name="citation",
                type=SearchFieldDataType.String,
                filterable=False
            ),

            # Applies to which entities (RUMC, RMG, etc.)
            SimpleField(
                name="applies_to",
                type=SearchFieldDataType.String,
                filterable=True,
                facetable=True
            ),

            # Date updated - for filtering by recency
            SimpleField(
                name="date_updated",
                type=SearchFieldDataType.String,
                filterable=True,
                sortable=True
            ),

            # Source file - for tracking
            SimpleField(
                name="source_file",
                type=SearchFieldDataType.String,
                filterable=True
            ),

            # Content hash - for differential sync
            SimpleField(
                name="content_hash",
                type=SearchFieldDataType.String,
                filterable=True
            ),

            # Document owner - for accountability tracking
            SimpleField(
                name="document_owner",
                type=SearchFieldDataType.String,
                filterable=True
            ),

            # Date approved - for compliance tracking
            SimpleField(
                name="date_approved",
                type=SearchFieldDataType.String,
                filterable=True
            ),

            # Entity-specific boolean filters (for efficient O(1) filtering)
            SimpleField(
                name="applies_to_rumc",
                type=SearchFieldDataType.Boolean,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="applies_to_rumg",
                type=SearchFieldDataType.Boolean,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="applies_to_rmg",
                type=SearchFieldDataType.Boolean,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="applies_to_roph",
                type=SearchFieldDataType.Boolean,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="applies_to_rcmc",
                type=SearchFieldDataType.Boolean,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="applies_to_rch",
                type=SearchFieldDataType.Boolean,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="applies_to_roppg",
                type=SearchFieldDataType.Boolean,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="applies_to_rcmg",
                type=SearchFieldDataType.Boolean,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="applies_to_ru",
                type=SearchFieldDataType.Boolean,
                filterable=True,
                facetable=True
            ),

            # Hierarchical chunking fields
            SearchableField(
                name="chunk_level",
                type=SearchFieldDataType.String,
                filterable=True
            ),
            SimpleField(
                name="parent_chunk_id",
                type=SearchFieldDataType.String,
                filterable=True
            ),
            SimpleField(
                name="chunk_index",
                type=SearchFieldDataType.Int32,
                sortable=True
            ),

            # Enhanced metadata fields
            SearchableField(
                name="category",
                type=SearchFieldDataType.String,
                filterable=True,
                facetable=True
            ),
            SearchableField(
                name="subcategory",
                type=SearchFieldDataType.String,
                filterable=True,
                facetable=True
            ),
            SearchableField(
                name="regulatory_citations",
                type=SearchFieldDataType.String
            ),
            SearchableField(
                name="related_policies",
                type=SearchFieldDataType.String
            ),

            # Version control fields (for monthly update tracking v1 → v2 transitions)
            SimpleField(
                name="version_number",
                type=SearchFieldDataType.String,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="version_date",
                type=SearchFieldDataType.DateTimeOffset,
                filterable=True,
                sortable=True
            ),
            SimpleField(
                name="effective_date",
                type=SearchFieldDataType.DateTimeOffset,
                filterable=True,
                sortable=True
            ),
            SimpleField(
                name="expiration_date",
                type=SearchFieldDataType.DateTimeOffset,
                filterable=True,
                sortable=True
            ),
            SimpleField(
                name="policy_status",
                type=SearchFieldDataType.String,
                filterable=True,
                facetable=True  # ACTIVE, SUPERSEDED, RETIRED, DRAFT
            ),
            SimpleField(
                name="superseded_by",
                type=SearchFieldDataType.String,
                filterable=True
            ),
            SimpleField(
                name="version_sequence",
                type=SearchFieldDataType.Int32,
                filterable=True,
                sortable=True
            ),
        ]

        # Vector search configuration
        vector_search = VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(
                    name="hnsw-algo",
                    parameters={
                        "m": 4,
                        "efConstruction": 400,
                        "efSearch": 500,
                        "metric": "cosine"
                    }
                )
            ],
            profiles=[
                VectorSearchProfile(
                    name="default-profile",
                    algorithm_configuration_name="hnsw-algo",
                    vectorizer_name="aoai-vectorizer"
                )
            ],
            vectorizers=[
                AzureOpenAIVectorizer(
                    vectorizer_name="aoai-vectorizer",
                    parameters=AzureOpenAIVectorizerParameters(
                        resource_url=self.aoai_endpoint,
                        deployment_name=self.embedding_deployment,
                        model_name="text-embedding-3-large",
                        api_key=self.aoai_api_key
                    )
                )
            ]
        )

        # Semantic search configuration for re-ranking
        semantic_config = SemanticConfiguration(
            name="default-semantic",
            prioritized_fields=SemanticPrioritizedFields(
                content_fields=[SemanticField(field_name="content")],
                title_field=SemanticField(field_name="title"),
                keywords_fields=[SemanticField(field_name="section")]
            )
        )

        semantic_search = SemanticSearch(
            configurations=[semantic_config],
            default_configuration_name="default-semantic"
        )

        # Create index
        index = SearchIndex(
            name=self.index_name,
            fields=fields,
            vector_search=vector_search,
            semantic_search=semantic_search
        )

        self.index_client.create_or_update_index(index)
        logger.info(f"Index '{self.index_name}' created/updated successfully")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((HttpResponseError, ConnectionError, TimeoutError))
    )
    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text using Azure OpenAI with retry logic."""
        response = self.aoai_client.embeddings.create(
            input=text,
            model=self.embedding_deployment
        )
        return response.data[0].embedding

    def _upload_batch_with_retry(
        self,
        documents: List[dict],
        max_retries: int = 3
    ) -> Tuple[int, int]:
        """
        Upload a batch of documents with retry logic for partial failures (HTTP 207).

        Azure AI Search returns 207 when some documents succeed and others fail.
        The SDK auto-retries 503 (Service Unavailable) but NOT 207 partial failures.
        This method implements retry logic for failed documents in 207 responses.

        Args:
            documents: List of document dicts to upload
            max_retries: Maximum retry attempts for failed documents

        Returns:
            Tuple of (succeeded_count, failed_count)
        """
        succeeded = 0
        failed_docs = documents.copy()

        for attempt in range(max_retries):
            if not failed_docs:
                break

            try:
                result = self.search_client.upload_documents(documents=failed_docs)

                # Process results - separate succeeded from failed
                new_failed = []
                for i, r in enumerate(result):
                    if r.succeeded:
                        succeeded += 1
                    else:
                        # Log the specific error for debugging
                        logger.warning(f"Document upload failed: {r.key} - {r.error_message}")
                        new_failed.append(failed_docs[i])

                failed_docs = new_failed

                if failed_docs and attempt < max_retries - 1:
                    # Exponential backoff before retry
                    wait_time = (2 ** attempt) * 0.5  # 0.5s, 1s, 2s
                    logger.info(f"Retrying {len(failed_docs)} failed documents in {wait_time}s (attempt {attempt + 2}/{max_retries})")
                    time.sleep(wait_time)

            except HttpResponseError as e:
                logger.error(f"Batch upload HTTP error (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    return succeeded, len(failed_docs)
                time.sleep(2 ** attempt)

        return succeeded, len(failed_docs)

    def upload_chunks(
        self,
        chunks: List[PolicyChunk],
        batch_size: int = 100,
        generate_embeddings: bool = True
    ) -> Dict[str, int]:
        """
        Upload chunks to Azure Search index with production-grade error handling.

        Features:
        - Batched uploads (default 100 docs per batch, Azure max is 1000)
        - Retry logic for HTTP 207 partial failures
        - Exponential backoff for transient errors
        - Detailed logging of failed documents

        Args:
            chunks: List of PolicyChunk objects
            batch_size: Number of chunks per upload batch (max 1000)
            generate_embeddings: Whether to generate embeddings (set False if using integrated vectorizer)

        Returns:
            Dict with 'uploaded' and 'failed' counts
        """
        # Azure limit is 1000 docs per batch, 16MB payload
        batch_size = min(batch_size, 1000)

        stats = {'uploaded': 0, 'failed': 0}
        documents = []

        for chunk in chunks:
            doc = chunk.to_azure_document()

            # Generate embedding if requested
            if generate_embeddings:
                try:
                    doc['content_vector'] = self.generate_embedding(chunk.text)
                except Exception as e:
                    logger.warning(f"Embedding failed for {chunk.chunk_id}: {e}")
                    stats['failed'] += 1
                    continue

            documents.append(doc)

            # Upload in batches
            if len(documents) >= batch_size:
                succeeded, failed = self._upload_batch_with_retry(documents)
                stats['uploaded'] += succeeded
                stats['failed'] += failed
                documents = []

        # Upload remaining documents
        if documents:
            succeeded, failed = self._upload_batch_with_retry(documents)
            stats['uploaded'] += succeeded
            stats['failed'] += failed

        logger.info(f"Uploaded {stats['uploaded']} chunks, {stats['failed']} failed")
        return stats

    def delete_chunks(self, chunk_ids: List[str]) -> int:
        """
        Delete chunks by ID.

        Args:
            chunk_ids: List of chunk IDs to delete

        Returns:
            Number of successfully deleted chunks
        """
        if not chunk_ids:
            return 0

        documents = [{"id": cid} for cid in chunk_ids]
        result = self.search_client.delete_documents(documents=documents)
        deleted = len([r for r in result if r.succeeded])
        logger.info(f"Deleted {deleted} chunks")
        return deleted

    def delete_by_source_file(self, source_file: str) -> int:
        """
        Delete all chunks from a specific source file.

        Used when a document is updated - delete old chunks before uploading new ones.
        Handles pagination for source files with >1000 chunks.
        """
        total_deleted = 0
        batch_count = 0

        # Paginate through all chunks from this source file
        # Azure Search returns max 1000 results per query
        while True:
            batch_count += 1
            results = self.search_client.search(
                search_text="*",
                filter=f"source_file eq '{source_file}'",
                select=["id"],
                top=1000
            )

            chunk_ids = [r['id'] for r in results]

            if not chunk_ids:
                break

            deleted = self.delete_chunks(chunk_ids)
            total_deleted += deleted

            logger.debug(f"Delete batch {batch_count}: removed {deleted} chunks from {source_file}")

            # If we got fewer than 1000, we've reached the end
            if len(chunk_ids) < 1000:
                break

            # Safety limit to prevent infinite loops
            if batch_count > 100:
                logger.warning(f"Delete pagination exceeded 100 batches for {source_file}, stopping")
                break

        if total_deleted > 0:
            logger.info(f"Deleted {total_deleted} total chunks from {source_file} ({batch_count} batches)")

        return total_deleted

    def search(
        self,
        query: str,
        top: int = 5,
        filter_expr: Optional[str] = None,
        use_semantic_ranking: bool = True,
        use_fuzzy: bool = True
    ) -> List[SearchResult]:
        """
        Hybrid search combining vector + keyword + semantic ranking.

        This is optimized for literal text retrieval:
        - Vector search finds semantically similar chunks
        - Keyword search finds exact term matches (with optional fuzzy matching)
        - Synonym expansion via synonym map
        - Semantic ranking re-orders by relevance

        Args:
            query: User's search query
            top: Number of results to return
            filter_expr: Optional OData filter (e.g., "applies_to eq 'RMG'")
            use_semantic_ranking: Whether to apply semantic re-ranking
            use_fuzzy: Whether to apply fuzzy matching for typo tolerance

        Returns:
            List of SearchResult objects with content and citations
        """
        # Calculate k for vector search - need more candidates when filtering
        # Filters reduce result pool, so we need a larger k to ensure quality results
        if filter_expr:
            # When filtering, use a larger k to ensure enough candidates after filter
            semantic_k = max(100, top * 10) if use_semantic_ranking else max(50, top * 5)
        else:
            semantic_k = max(50, top * 5) if use_semantic_ranking else max(20, top * 3)

        # Vector query for semantic search using VectorizableTextQuery
        # This uses the integrated vectorizer defined in the index to generate embeddings
        vector_query = VectorizableTextQuery(
            text=query,
            k=semantic_k,  # More candidates for better re-ranking
            fields="content_vector"
        )

        search_params = {
            "search_text": query,
            "vector_queries": [vector_query],
            "select": [
                "content",
                "citation",
                "title",
                "section",
                "applies_to",
                "date_updated",
                "reference_number",
                "source_file",
                "document_owner",
                "date_approved",
                # Entity booleans
                "applies_to_rumc",
                "applies_to_rumg",
                "applies_to_rmg",
                "applies_to_roph",
                "applies_to_rcmc",
                "applies_to_rch",
                "applies_to_roppg",
                "applies_to_rcmg",
                "applies_to_ru",
                # Hierarchical fields
                "chunk_level",
                "parent_chunk_id",
                "chunk_index",
                # Enhanced metadata
                "category",
                "subcategory",
                "regulatory_citations",
                "related_policies",
            ],
            "top": top,
        }

        if filter_expr:
            search_params["filter"] = filter_expr

        # Semantic ranking provides best results and handles typos via embedding similarity
        # Note: Synonym maps are applied at index time for keyword matching
        # Check if semantic search is disabled (e.g., quota exceeded)
        disable_semantic = os.environ.get("DISABLE_SEMANTIC_SEARCH", "").lower() == "true"
        if use_semantic_ranking and not disable_semantic:
            search_params["query_type"] = "semantic"
            search_params["semantic_configuration_name"] = "default-semantic"
            # Enable speller for typo correction with semantic search
            search_params["query_language"] = "en-us"
            search_params["query_speller"] = "lexicon"
        elif use_semantic_ranking and disable_semantic:
            logger.info("Semantic search disabled via DISABLE_SEMANTIC_SEARCH env var, using simple search")
        elif use_fuzzy:
            # Fallback to fuzzy search if semantic ranking is disabled
            # Apply fuzzy matching to each word in the query for typo tolerance
            fuzzy_query = " ".join(
                f"{word}~1" if len(word) >= 4 else word
                for word in query.split()
            )
            search_params["search_text"] = fuzzy_query
            search_params["query_type"] = "full"  # Required for fuzzy search syntax

        # Execute search with performance tracking
        start_time = time.time()
        try:
            results = self.search_client.search(**search_params)

            search_results = []
            for result in results:
                sr = SearchResult(
                    content=result.get("content", ""),
                    citation=result.get("citation", ""),
                    title=result.get("title", ""),
                    section=result.get("section", ""),
                    applies_to=result.get("applies_to", ""),
                    date_updated=result.get("date_updated", ""),
                    score=result.get("@search.score", 0),
                    reference_number=result.get("reference_number", ""),
                    reranker_score=result.get("@search.reranker_score"),
                    source_file=result.get("source_file", ""),
                    document_owner=result.get("document_owner", ""),
                    date_approved=result.get("date_approved", ""),
                    # Entity booleans
                    applies_to_rumc=result.get("applies_to_rumc", False),
                    applies_to_rumg=result.get("applies_to_rumg", False),
                    applies_to_rmg=result.get("applies_to_rmg", False),
                    applies_to_roph=result.get("applies_to_roph", False),
                    applies_to_rcmc=result.get("applies_to_rcmc", False),
                    applies_to_rch=result.get("applies_to_rch", False),
                    applies_to_roppg=result.get("applies_to_roppg", False),
                    applies_to_rcmg=result.get("applies_to_rcmg", False),
                    applies_to_ru=result.get("applies_to_ru", False),
                    # Hierarchical fields
                    chunk_level=result.get("chunk_level", "semantic"),
                    parent_chunk_id=result.get("parent_chunk_id"),
                    chunk_index=result.get("chunk_index", 0),
                    # Enhanced metadata
                    category=result.get("category"),
                    subcategory=result.get("subcategory"),
                    regulatory_citations=result.get("regulatory_citations"),
                    related_policies=result.get("related_policies"),
                )
                search_results.append(sr)

            # Performance logging
            elapsed = time.time() - start_time
            logger.info(
                f"Search completed: {elapsed:.2f}s | "
                f"Results: {len(search_results)} | "
                f"Query: '{query[:50]}{'...' if len(query) > 50 else ''}' | "
                f"Filter: {filter_expr or 'none'} | "
                f"Semantic: {use_semantic_ranking}"
            )

            return search_results

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Search failed after {elapsed:.2f}s: {type(e).__name__}: {e}")
            raise

    def get_chunk_by_id(self, chunk_id: str) -> Optional[Dict]:
        """Retrieve a specific chunk by ID."""
        try:
            return self.search_client.get_document(key=chunk_id)
        except ResourceNotFoundError:
            logger.debug(f"Chunk not found: {chunk_id}")
            return None
        except HttpResponseError as e:
            logger.warning(f"HTTP error retrieving chunk {chunk_id}: {e}")
            return None

    def get_metadata_by_source_file(self, source_file: str) -> Optional[Dict]:
        """
        Retrieve metadata for a document by its source_file.

        Returns the first chunk's metadata (applies_to, reference_number, etc.)
        for the given source file.

        Args:
            source_file: The source PDF filename (e.g., "hr-001.pdf")

        Returns:
            Dict with metadata fields or None if not found
        """
        if not source_file:
            return None

        try:
            # Search for documents with this source_file
            results = self.search_client.search(
                search_text="*",
                filter=f"source_file eq '{source_file}'",
                select=[
                    "applies_to",
                    "reference_number",
                    "section",
                    "date_updated",
                    "document_owner",
                    "date_approved",
                    "title",
                    # Entity booleans for building applies_to string
                    "applies_to_rumc",
                    "applies_to_rumg",
                    "applies_to_rmg",
                    "applies_to_roph",
                    "applies_to_rcmc",
                    "applies_to_rch",
                    "applies_to_roppg",
                    "applies_to_rcmg",
                    "applies_to_ru",
                ],
                top=1
            )

            for result in results:
                # Build applies_to string from boolean fields if not present
                applies_to = result.get("applies_to", "")
                if not applies_to:
                    # Build from boolean fields
                    entities = []
                    if result.get("applies_to_rumc"): entities.append("RUMC")
                    if result.get("applies_to_rumg"): entities.append("RUMG")
                    if result.get("applies_to_rmg"): entities.append("RMG")
                    if result.get("applies_to_roph"): entities.append("ROPH")
                    if result.get("applies_to_rcmc"): entities.append("RCMC")
                    if result.get("applies_to_rch"): entities.append("RCH")
                    if result.get("applies_to_roppg"): entities.append("ROPPG")
                    if result.get("applies_to_rcmg"): entities.append("RCMG")
                    if result.get("applies_to_ru"): entities.append("RU")
                    applies_to = ", ".join(entities) if entities else ""

                return {
                    "applies_to": applies_to,
                    "reference_number": result.get("reference_number", ""),
                    "section": result.get("section", ""),
                    "date_updated": result.get("date_updated", ""),
                    "document_owner": result.get("document_owner", ""),
                    "date_approved": result.get("date_approved", ""),
                    "title": result.get("title", ""),
                }

            logger.debug(f"No documents found for source_file: {source_file}")
            return None

        except HttpResponseError as e:
            logger.warning(f"HTTP error getting metadata for {source_file}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error getting metadata for {source_file}: {e}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics."""
        try:
            index = self.index_client.get_index(self.index_name)
            # Count documents
            results = self.search_client.search(
                search_text="*",
                include_total_count=True,
                top=0
            )
            return {
                "index_name": self.index_name,
                "document_count": results.get_count(),
                "fields": len(index.fields),
            }
        except ResourceNotFoundError:
            return {"error": "Index not found", "index_name": self.index_name}
        except HttpResponseError as e:
            logger.error(f"HTTP error getting stats: {e}")
            return {"error": str(e)}

    def close(self) -> None:
        """
        Clean up resources.

        Should be called during application shutdown to release connections.
        """
        if self.aoai_client is not None:
            try:
                self.aoai_client.close()
                logger.info("PolicySearchIndex AOAI client closed")
            except Exception as e:
                logger.warning(f"Error closing AOAI client: {e}")

        if self.search_client is not None:
            try:
                if hasattr(self.search_client, 'close'):
                    self.search_client.close()
                logger.info("PolicySearchIndex search client closed")
            except Exception as e:
                logger.warning(f"Error closing search client: {e}")

        if self.index_client is not None:
            try:
                if hasattr(self.index_client, 'close'):
                    self.index_client.close()
                logger.info("PolicySearchIndex index client closed")
            except Exception as e:
                logger.warning(f"Error closing index client: {e}")


def format_rag_context(results: List[SearchResult]) -> str:
    """
    Format search results as context for RAG prompt with relevance indicators.

    This creates a context block that encourages literal retrieval:
    - Each chunk is clearly delimited with relevance score
    - Citations are prominently displayed
    - No synthesis encouraged
    """
    if not results:
        return "No relevant policy documents found."

    context_parts = []
    for i, result in enumerate(results, 1):
        # Show relevance score to help LLM prioritize
        if result.reranker_score:
            confidence = f"Relevance: {result.reranker_score:.2f}"
        else:
            confidence = f"Score: {result.score:.2f}"

        context_parts.append(f"""
═══════════════════════════════════════════════════════════════
 POLICY CHUNK {i} ({confidence})
═══════════════════════════════════════════════════════════════
{result.format_for_rag()}
""")

    return "\n".join(context_parts)


# CLI for testing
if __name__ == "__main__":
    import sys
    from preprocessing.chunker import PolicyChunker

    print("=" * 60)
    print("AZURE POLICY INDEX - Setup and Test")
    print("=" * 60)

    # Check environment
    if not SEARCH_API_KEY:
        print("ERROR: SEARCH_API_KEY not set in environment")
        sys.exit(1)

    if not AOAI_API_KEY:
        print("ERROR: AOAI_API (Azure OpenAI API key) not set in environment")
        sys.exit(1)

    # Initialize index
    index = PolicySearchIndex()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "create":
            print("\nCreating index...")
            index.create_index()

        elif command == "upload" and len(sys.argv) > 2:
            folder = sys.argv[2]
            print(f"\nProcessing and uploading from {folder}...")

            # Chunk documents
            chunker = PolicyChunker(max_chunk_size=1500)
            result = chunker.process_folder(folder)

            print(f"Chunked {result['stats']['total_docs']} docs into {result['stats']['total_chunks']} chunks")

            # Upload to index
            index.upload_chunks(result['chunks'])

        elif command == "search" and len(sys.argv) > 2:
            query = " ".join(sys.argv[2:])
            print(f"\nSearching for: {query}")

            results = index.search(query, top=3)

            for i, r in enumerate(results, 1):
                print(f"\n--- Result {i} (score: {r.score:.2f}) ---")
                print(f"Citation: {r.citation}")
                print(f"Content: {r.content[:300]}...")

        elif command == "stats":
            stats = index.get_stats()
            print(f"\nIndex Stats: {stats}")

        elif command == "synonyms":
            print("\nUpdating synonym map...")
            index.create_synonym_map()
            # Count synonym rules
            synonym_rules = [
                line.strip() for line in SYNONYMS.strip().split('\n')
                if line.strip() and not line.strip().startswith('#')
            ]
            print(f"Synonym map '{SYNONYM_MAP_NAME}' updated with {len(synonym_rules)} rules")
            print("\nSample rules:")
            for rule in synonym_rules[:5]:
                print(f"  - {rule[:80]}{'...' if len(rule) > 80 else ''}")

        elif command == "test-synonyms" and len(sys.argv) > 2:
            query = " ".join(sys.argv[2:])
            print(f"\nTesting synonym expansion for: '{query}'")
            print("-" * 60)

            # Test query-time expansion via SynonymService
            try:
                from app.services.synonym_service import get_synonym_service
                svc = get_synonym_service()
                result = svc.expand_query(query)
                print(f"Query-time expansion: '{result.expanded_query}'")
                if result.abbreviations_expanded:
                    print(f"  Abbreviations: {result.abbreviations_expanded}")
                if result.misspellings_corrected:
                    print(f"  Misspellings: {result.misspellings_corrected}")
            except Exception as e:
                print(f"Query expansion unavailable: {e}")

            # Test search with synonyms
            print(f"\nSearching with expanded query...")
            results = index.search(query, top=3)
            for i, r in enumerate(results, 1):
                print(f"\n  Result {i}: {r.title}")
                print(f"    Score: {r.score:.2f} | Reranker: {r.reranker_score or 'N/A'}")
                print(f"    Content: {r.content[:150]}...")

        else:
            print("Usage:")
            print("  python azure_policy_index.py create                  # Create index")
            print("  python azure_policy_index.py upload <folder>         # Upload chunks")
            print("  python azure_policy_index.py search <query>          # Test search")
            print("  python azure_policy_index.py stats                   # Get stats")
            print("  python azure_policy_index.py synonyms                # Update synonym map")
            print("  python azure_policy_index.py test-synonyms <query>   # Test synonym expansion")
    else:
        print("\nRun with 'create', 'upload', 'search', 'stats', 'synonyms', or 'test-synonyms' command")
