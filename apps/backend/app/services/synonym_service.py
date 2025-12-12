"""
Synonym Expansion Service for RUSH Policy RAG

Enhances search accuracy by expanding user queries with synonyms,
handling medical abbreviations, misspellings, and Rush-specific terms.

Uses semantic-search-synonyms.json which contains:
- 1,860 policy documents analyzed
- 20+ synonym categories (medical abbreviations, hospital codes, etc.)
- Rush-specific institutional terms (RUMC, RUMG, ROPH, etc.)
- Common misspellings

Integration points:
1. Query preprocessing before search
2. Agent prompt context
3. Misspelling correction
"""

import json
import re
import logging
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Path to synonym configuration
SYNONYMS_PATH = Path(__file__).resolve().parent.parent.parent.parent.parent / "semantic-search-synonyms.json"

# Compound term detection patterns for domain-specific expansion
# When two related terms appear together, add contextual synonyms
# CRITICAL: Include exact policy title phrases for better retrieval
COMPOUND_EXPANSIONS = {
    # NICU/Neonatal - Include "Neonatal ICU" exact phrase for title matching
    ('nicu', 'pain'): 'Neonatal ICU pain assessment neonatal FLACC N-PASS infant',
    ('neonatal', 'pain'): 'Neonatal ICU NICU pain assessment infant newborn FLACC N-PASS',
    ('nicu', 'nursing'): 'Neonatal ICU neonatal nursing care infant newborn',
    ('neonatal', 'nursing'): 'Neonatal ICU NICU nursing care infant newborn',
    ('nicu', 'policy'): 'Neonatal ICU neonatal newborn infant intensive care',
    ('neonatal', 'policy'): 'Neonatal ICU NICU newborn infant intensive care',
    # PICU/Pediatric - Include "Pediatric ICU" exact phrase
    ('picu', 'pain'): 'Pediatric ICU pain assessment pediatric FLACC Wong-Baker child',
    ('pediatric', 'pain'): 'Pediatric ICU PICU pain assessment child FLACC Wong-Baker',
    ('pediatric', 'nursing'): 'Pediatric ICU PICU child nursing care',
    ('pediatric', 'policy'): 'Pediatric ICU PICU child children kids',
    # ED/Emergency
    ('ed', 'pain'): 'emergency department pain assessment triage pain score',
    ('emergency', 'pain'): 'ED emergency department pain assessment triage',
    # ICU general
    ('icu', 'pain'): 'intensive care pain assessment sedation CPOT critical care',
    # OB/L&D
    ('labor', 'pain'): 'labor and delivery pain assessment obstetric epidural L&D',
    ('postpartum', 'pain'): 'postpartum pain assessment delivery recovery cesarean',
    ('delivery', 'pain'): 'labor and delivery pain assessment L&D obstetric',
    # Generic "pain policy" -> "pain assessment" bridge
    ('pain', 'policy'): 'pain assessment pain management procedure',
    # Catheter-related compounds
    ('urinary', 'catheter'): 'Foley catheter indwelling bladder urinary',
    ('foley', 'catheter'): 'urinary catheter indwelling bladder',
    ('central', 'line'): 'central venous line CVC PICC central catheter',
    ('central', 'catheter'): 'CVC central venous line PICC',
    # Sedation-related compounds
    ('sedation', 'policy'): 'sedation mechanical ventilation IV sedation moderate procedural',
    ('sedation', 'protocol'): 'sedation mechanical ventilation IV sedation moderate',
    ('mechanical', 'ventilation'): 'sedation ventilator weaning respiratory',
    # End-of-life compounds
    ('advance', 'directive'): 'advance directive advance care planning DNR end-of-life',
    # Emergency code compounds
    ('code', 'blue'): 'code blue cardiac arrest resuscitation CPR',
    ('code', 'gray'): 'code gray combative patient behavioral emergency security',
    ('code', 'orange'): 'code orange hazmat chemical spill decontamination',
    ('code', 'purple'): 'code purple infant abduction missing child security',
    ('code', 'silver'): 'code silver active shooter weapon threat security',
    ('code', 'white'): 'code white IT telecom disruption downtime Epic',
    ('code', 'yellow'): 'code yellow bomb threat evacuation security',
    ('code', 'red'): 'code red fire emergency evacuation RACE PASS',
    ('code', 'pink'): 'code pink infant abduction pediatric security',
    # Compliance compounds
    ('prior', 'authorization'): 'prior authorization preauth approval insurance',
    ('charity', 'care'): 'charity care financial assistance uncompensated',
    ('identity', 'theft'): 'identity theft fraud security breach',
    # Safety compounds
    ('fire', 'safety'): 'fire safety prevention RACE PASS evacuation',
    ('fall', 'prevention'): 'fall prevention fall risk patient safety',
    ('needle', 'stick'): 'needlestick sharps injury bloodborne exposure',
    # Point of Care Testing
    ('point', 'care'): 'point of care testing POCT POC bedside',
    # Against Medical Advice / Elopement
    ('against', 'medical'): 'against medical advice AMA discharge leaving',
    ('code', 'gold'): 'code gold elopement missing patient AMA leaving',
    # Additional emergency codes (from environment-of-care policies)
    ('code', 'maroon'): 'code maroon bomb threat evacuation security',
    ('code', 'black'): 'code black severe weather tornado shelter',
    ('code', 'triage'): 'code triage mass casualty disaster MCI surge',
    ('code', 'green'): 'code green evacuation relocation all clear',
    # Emergency management
    ('incident', 'command'): 'incident command system ICS HICS emergency management',
    ('emergency', 'operations'): 'emergency operations plan EOP disaster preparedness',
    # Administrative/Compliance
    ('safe', 'haven'): 'safe haven newborn abandonment infant relinquishment',
    ('two', 'midnight'): 'two midnight rule inpatient observation billing',
    ('primary', 'source'): 'primary source verification PSV credentialing licensure',
    # Diagnostic imaging
    ('computed', 'tomography'): 'CT scan CAT scan imaging radiology',
    ('magnetic', 'resonance'): 'MRI magnetic resonance imaging scan radiology',
    # Verbal orders - help retrieve when asking about specific roles
    ('verbal', 'order'): 'verbal order telephone order VO TO accept receive authorized personnel not authorized scope',
    ('telephone', 'order'): 'telephone order verbal order TO VO accept receive authorized personnel not authorized scope',
    ('medical', 'assistant'): 'medical assistant MA verbal order telephone order authorized personnel not authorized scope of practice accept receive orders',
    ('unit', 'secretary'): 'unit secretary verbal order telephone order authorized personnel not authorized accept receive orders scope',
    ('nursing', 'aide'): 'nursing aide CNA certified nursing assistant verbal order telephone order authorized personnel not authorized scope',
    ('accept', 'order'): 'accept order receive verbal telephone authorized personnel not authorized RN nurse pharmacist LPN',
    ('can', 'accept'): 'accept verbal order telephone order authorized personnel not authorized RN LPN physician pharmacist nurse practitioner',
    ('authorized', 'personnel'): 'authorized personnel verbal order telephone order accept receive RN LPN physician pharmacist not authorized',
    # Hand-off communication
    ('hand', 'off'): 'handoff hand-off SBAR shift report patient handoff communication transfer',
    ('communication', 'framework'): 'SBAR handoff hand-off shift report communication transfer',
    ('shift', 'report'): 'shift report handoff SBAR communication patient status',
    # Oak Park specific
    ('oak', 'park'): 'Oak Park ROPH Rush Oak Park Hospital Rush Oak Park',
    # Research recruitment/advertising
    ('research', 'poster'): 'research poster advertisement recruitment IRB approval study flyer participant',
    ('research', 'recruit'): 'research recruitment participant enrollment IRB approval advertisement study subject',
    ('research', 'advertisement'): 'research advertisement poster recruitment IRB approval study flyer',
    ('research', 'study'): 'research study clinical trial IRB protocol participant subject',
    ('irb', 'recruitment'): 'IRB recruitment advertisement poster study participant enrollment approval',
    ('irb', 'poster'): 'IRB poster advertisement recruitment study approval flyer',
    ('hang', 'poster'): 'hang poster advertisement recruitment IRB approval study flyer',
    ('advertise', 'study'): 'advertise study recruitment poster IRB approval participant',
    # Adverse events
    ('adverse', 'event'): 'adverse event AE unanticipated problem safety event incident reporting',
    ('adverse', 'report'): 'adverse event report AE reporting safety incident unanticipated problem',
    ('health', 'episode'): 'health episode adverse event AE incident safety event unanticipated problem',
    ('report', 'adverse'): 'report adverse event AE safety incident unanticipated problem reporting',
    ('clinical', 'trial'): 'clinical trial research study IRB protocol adverse event participant',
    # Consent documentation
    ('consent', 'sign'): 'consent signature sign form documentation witness mark X',
    ('consent', 'signature'): 'consent signature sign form documentation witness mark X',
    ('can', 'sign'): 'sign signature mark X consent form witness documentation',
    ('subject', 'sign'): 'subject sign signature consent form mark X witness documentation',
    ('research', 'consent'): 'research consent informed consent form signature documentation IRB subject participant',
    ('consent', 'form'): 'consent form signature sign mark X witness documentation informed',
    # Patient consent/agreement language (addresses "agree to treatment" → informed consent gap)
    ('agree', 'treatment'): 'agree treatment informed consent consent form patient consent authorization procedure consent',
    ('patient', 'agree'): 'patient agree informed consent consent form authorization treatment consent patient rights',
    ('treatment', 'consent'): 'treatment consent informed consent patient consent authorization surgical consent procedure consent',
    ('agree', 'procedure'): 'agree procedure informed consent consent form surgical consent patient authorization',
    ('patient', 'consent'): 'patient consent informed consent consent form authorization treatment consent procedure consent',
    # Employee attendance/sick language (addresses "call off" → sick employee policy gap)
    ('call', 'off'): 'call off sick employee attendance absence unscheduled absence',
    ('calling', 'off'): 'calling off sick employee attendance absence',
    ('call', 'sick'): 'call sick employee attendance absence call off',
}

# Single-term expansions for key clinical terms
# Applied when term appears WITHOUT a compound match
SINGLE_TERM_EXPANSIONS = {
    'neonatal': 'NICU Neonatal ICU neonatal intensive care newborn infant',
    'pediatric': 'PICU Pediatric ICU pediatric intensive care child children',
    'pain': 'pain assessment pain management',
    'visitor': 'visitor visitation visiting hours guest',
    'restraint': 'restraint seclusion physical restraint chemical restraint',
    'fall': 'fall prevention fall risk patient falls',
    'medication': 'medication administration drug dispensing pharmacy',
    'infection': 'infection control infection prevention HAI',
    'transfusion': 'blood transfusion blood products blood administration',
    'consent': 'informed consent consent form authorization patient consent',
    'agree': 'agree consent authorization informed consent patient agreement',
    # Employee attendance
    'calloff': 'call off sick employee attendance absence',
    'sick': 'sick employee attendance absence call off unscheduled',
    # Catheter synonyms (user requested)
    'foley': 'urinary catheter indwelling bladder Foley',
    'catheter': 'urinary catheter Foley indwelling',
    'cvc': 'central venous catheter central line TLC PICC Quinton Trialysis',
    'tlc': 'triple lumen catheter central venous catheter CVC central line',
    'quinton': 'central venous catheter dialysis catheter CVC central line',
    'trialysis': 'central venous catheter dialysis catheter CVC central line',
    'central': 'central venous catheter central line CVC PICC TLC Quinton',
    'iv': 'peripheral intravenous PIV catheter',
    # Sedation synonyms (user requested)
    'sedation': 'sedation mechanical ventilation IV sedation moderate sedation',
    # Respiratory/ventilation synonyms (user requested)
    'hamilton': 'ventilator mechanical ventilation respiratory',
    'bipap': 'non-invasive positive pressure ventilation NIPPV BiPAP respiratory',
    'cpap': 'continuous positive airway pressure CPAP respiratory sleep apnea',
    'ventilator': 'mechanical ventilation Hamilton respiratory weaning',
    'hfnc': 'high-flow nasal cannula heated humidified oxygen therapy respiratory',
    # End-of-life/DNR synonyms (user requested)
    'dnr': 'do not resuscitate end-of-life advance care planning advance directive',
    'unilateral': 'unilateral do-not-resuscitate DNR',
    'end-of-life': 'end of life DNR advance care planning comfort care hospice',
    'comfort': 'comfort care end-of-life palliative hospice',
    # Hand-off communication single terms
    'handoff': 'hand-off handoff SBAR shift report patient communication transfer',
    'hand-off': 'handoff hand-off SBAR shift report patient communication transfer',
    'sbar': 'SBAR Situation Background Assessment Recommendation handoff hand-off shift report',
    # Tubes/Lines (common nursing equipment)
    'ngt': 'nasogastric tube NG tube feeding tube',
    'ogt': 'orogastric tube OG tube feeding tube',
    'ett': 'endotracheal tube intubation airway',
    'trach': 'tracheostomy tracheotomy airway',
    'dobhoff': 'small bore feeding tube nasogastric enteral',
    'peg': 'percutaneous endoscopic gastrostomy feeding tube enteral',
    'jp': 'Jackson-Pratt drain surgical drain wound',
    'hemovac': 'wound drain surgical drain drainage',
    # Monitoring/Assessment
    'tele': 'telemetry cardiac monitoring ECG EKG',
    'a-line': 'arterial line arterial catheter blood pressure monitoring',
    'swan': 'Swan-Ganz pulmonary artery catheter hemodynamic',
    'scd': 'sequential compression device DVT prevention pneumatic',
    # Cardiac/Vascular
    'afib': 'atrial fibrillation arrhythmia cardiac rhythm',
    'mi': 'myocardial infarction heart attack cardiac',
    'chf': 'congestive heart failure cardiac heart failure',
    'cva': 'cerebrovascular accident stroke brain',
    'dvt': 'deep vein thrombosis blood clot venous thromboembolism VTE',
    'pe': 'pulmonary embolism blood clot lung',
    # Critical conditions
    'ards': 'acute respiratory distress syndrome respiratory failure lung',
    'aki': 'acute kidney injury renal failure kidney',
    'dka': 'diabetic ketoacidosis diabetes glucose insulin',
    'sepsis': 'sepsis infection systemic inflammatory',
    'ams': 'altered mental status confusion consciousness neurological',
    # Emergency Codes (from environment-of-care policies)
    'code': 'emergency code hospital emergency',
    'gray': 'code gray combative patient security behavioral',
    'orange': 'code orange hazmat chemical spill decontamination',
    'purple': 'code purple infant abduction child missing',
    'silver': 'code silver active shooter weapon security',
    'triage': 'code triage mass casualty disaster MCI',
    'white': 'code white IT telecom disruption downtime',
    'yellow': 'code yellow bomb threat evacuation',
    # Compliance/Billing (from corporate-compliance policies)
    'hipaa': 'HIPAA privacy security patient information PHI',
    'emtala': 'EMTALA emergency medical treatment labor act screening',
    'abn': 'advanced beneficiary notice ABN non-coverage Medicare',
    'cdm': 'charge description master CDM pricing billing',
    'fmv': 'fair market value FMV compensation arrangement',
    'pos': 'place of service POS codes billing location',
    'stark': 'Stark law self-referral prohibition physician',
    'kickback': 'anti-kickback kickback prohibition AKS',
    # Safety/Environment of Care
    'ppe': 'personal protective equipment PPE safety gown gloves mask',
    'ilsm': 'interim life safety measures ILSM fire construction',
    'rmw': 'regulated medical waste RMW biohazard sharps',
    'sds': 'safety data sheet SDS MSDS chemical hazard',
    'usp': 'USP 800 hazardous drug handling compounding',
    'bloodborne': 'bloodborne pathogen exposure BBP needlestick',
    # HR/Administrative
    'fmla': 'Family Medical Leave Act FMLA leave absence',
    'ada': 'Americans with Disabilities Act ADA accommodation',
    'eeo': 'equal employment opportunity EEO discrimination',
    'onboarding': 'onboarding orientation new hire employee',
    'credentialing': 'credentialing privileging verification PSV',
    'psv': 'primary source verification PSV credentialing license',
    # IT/Cyber
    'downtime': 'downtime system outage IT disruption Epic',
    'phishing': 'phishing cyber security email scam',
    'mfa': 'multi-factor authentication MFA two-factor security',
    # Point of Care Testing / Lab
    'poct': 'point of care testing POC bedside glucose A1C',
    'poc': 'point of care testing POCT bedside',
    'a1c': 'hemoglobin A1C HbA1C glycated hemoglobin diabetes',
    'hba1c': 'hemoglobin A1C A1c glycated hemoglobin diabetes',
    'inr': 'international normalized ratio INR anticoagulation warfarin coumadin',
    # Diagnostic Imaging
    'ekg': 'electrocardiogram ECG cardiac heart rhythm',
    'ecg': 'electrocardiogram EKG cardiac heart rhythm',
    'eeg': 'electroencephalogram brain wave seizure neurology',
    'ct': 'computed tomography CAT scan imaging radiology',
    'mri': 'magnetic resonance imaging MRI radiology scan',
    'pet': 'positron emission tomography PET scan nuclear imaging oncology',
    'spect': 'single photon emission computed tomography SPECT nuclear imaging',
    'fluoro': 'fluoroscopy fluoroscopic imaging radiation live x-ray',
    'xray': 'x-ray radiograph imaging radiology',
    # Pre-operative
    'npo': 'nothing by mouth nil per os fasting surgery preop',
    'preop': 'preoperative pre-op surgery preparation NPO',
    # Staff Roles
    'aprn': 'advanced practice registered nurse NP nurse practitioner',
    'np': 'nurse practitioner APRN advanced practice',
    'lpn': 'licensed practical nurse LPN practical nursing',
    'cna': 'certified nursing assistant CNA aide patient care',
    'rn': 'registered nurse RN nursing staff',
    'md': 'physician doctor medical doctor attending',
    'pa': 'physician assistant PA-C provider',
    # Equipment/Devices
    'vad': 'ventricular assist device VAD LVAD heart failure mechanical',
    'lvad': 'left ventricular assist device VAD heart failure mechanical',
    'dme': 'durable medical equipment DME supplies wheelchair walker',
    'iabp': 'intra-aortic balloon pump IABP cardiac support counterpulsation',
    # Administrative/Discharge
    'ama': 'against medical advice AMA discharge patient leaving elopement',
    'elopement': 'elopement AMA code gold missing patient leaving',
    # Renal/Dialysis
    'esrd': 'end stage renal disease ESRD dialysis kidney failure',
    'dialysis': 'dialysis ESRD hemodialysis peritoneal renal',
    'hd': 'hemodialysis HD dialysis renal',
    # Infectious Disease
    'tb': 'tuberculosis TB PPD screening lung infection respiratory',
    'ppd': 'tuberculin skin test PPD TB tuberculosis screening',
    'mrsa': 'methicillin-resistant Staphylococcus aureus MRSA infection isolation',
    'vre': 'vancomycin-resistant Enterococcus VRE infection isolation',
    'cdiff': 'Clostridioides difficile C. diff infection isolation diarrhea',
    # Emergency Management
    'ics': 'incident command system ICS HICS emergency management',
    'hics': 'hospital incident command system HICS ICS emergency',
    'eop': 'emergency operations plan EOP disaster preparedness',
    'hva': 'hazard vulnerability analysis HVA risk assessment emergency',
    'mci': 'mass casualty incident MCI disaster surge triage',
    # EMS/Transport
    'ems': 'emergency medical services EMS ambulance paramedic',
    # Environmental Services
    'evs': 'environmental services EVS housekeeping cleaning sanitation',
    # Radiation Safety
    'alara': 'as low as reasonably achievable ALARA radiation safety exposure',
    'ram': 'radioactive materials RAM radiation nuclear medicine',
    # Security/Violence Prevention
    'bart': 'behavioral assessment response team BART violence workplace security',
    'wds': 'weapons detection system WDS security screening metal detector',
    # Research/Compliance
    'ctms': 'clinical trial management system CTMS research study',
    'irb': 'institutional review board IRB research ethics human subjects',
    # Research recruitment/advertising
    'poster': 'poster advertisement flyer recruitment IRB research study',
    'advertisement': 'advertisement poster flyer recruitment IRB research study',
    'recruit': 'recruit recruitment participant enrollment study research subject',
    'recruitment': 'recruitment participant enrollment study research advertisement poster',
    'participant': 'participant subject research study enrollment recruitment',
    # Consent documentation
    'signature': 'signature sign mark X consent form documentation witness',
    'sign': 'sign signature mark X consent form documentation',
    # Adverse events
    'adverse': 'adverse event AE safety incident unanticipated problem',
    'unanticipated': 'unanticipated problem adverse event AE safety incident',
    'episode': 'episode event incident adverse safety unanticipated problem',
    # Payment/Financial Compliance
    'pci': 'payment card industry PCI compliance credit card security',
    'fwa': 'fraud waste abuse FWA compliance Medicare Medicaid',
    # Additional Emergency Codes
    'maroon': 'code maroon bomb threat evacuation security',
    'black': 'code black severe weather tornado shelter',
    'gold': 'code gold elopement missing patient AMA',
    'green': 'code green evacuation relocation all clear',
}


@dataclass
class QueryExpansion:
    """Result of query expansion."""
    original_query: str
    expanded_query: str
    expansions_applied: List[Dict[str, str]] = field(default_factory=list)
    misspellings_corrected: List[Dict[str, str]] = field(default_factory=list)
    abbreviations_expanded: List[Dict[str, str]] = field(default_factory=list)


class SynonymService:
    """
    Service for expanding queries with synonyms to improve RAG accuracy.

    Expansion strategy:
    1. Correct common misspellings
    2. Expand medical abbreviations (e.g., "ED" → "emergency department")
    3. Add Rush-specific term alternatives
    4. Apply query expansion rules based on patterns

    The expanded query helps Azure AI Search find more relevant results
    even when users use different terminology than the indexed documents.
    """

    def __init__(self, synonyms_path: Optional[Path] = None):
        self.synonyms_path = synonyms_path or SYNONYMS_PATH
        self.synonym_groups: Dict[str, Dict] = {}
        self.category_keywords: Dict[str, List[str]] = {}
        self.query_expansion_rules: List[Dict] = []
        self.metadata: Dict = {}

        # Reverse lookup: term → canonical form
        self._term_to_canonical: Dict[str, str] = {}
        # Abbreviation lookup: abbrev → full form
        self._abbreviations: Dict[str, str] = {}
        # Misspelling lookup: misspelled → correct
        self._misspellings: Dict[str, str] = {}
        # Rush-specific terms
        self._rush_terms: Dict[str, List[str]] = {}

        self._load_synonyms()

    def _load_synonyms(self):
        """Load and index synonyms from JSON file."""
        if not self.synonyms_path.exists():
            logger.warning(f"Synonyms file not found: {self.synonyms_path}")
            return

        try:
            with open(self.synonyms_path, 'r') as f:
                data = json.load(f)

            self.metadata = data.get('metadata', {})
            self.synonym_groups = data.get('synonym_groups', {})
            self.category_keywords = data.get('category_keywords', {})
            self.query_expansion_rules = data.get('query_expansion_rules', {}).get('rules', [])

            # Build indexes
            self._build_indexes()

            logger.info(
                f"Loaded synonyms: {self.metadata.get('total_documents_analyzed', 0)} docs, "
                f"{len(self.synonym_groups)} groups, "
                f"{len(self._abbreviations)} abbreviations"
            )
        except Exception as e:
            logger.error(f"Failed to load synonyms: {e}")

    def _build_indexes(self):
        """Build reverse lookup indexes for fast query expansion."""
        # Medical abbreviations
        if 'medical_abbreviations' in self.synonym_groups:
            mappings = self.synonym_groups['medical_abbreviations'].get('mappings', {})
            for abbrev, synonyms in mappings.items():
                # Store abbreviation → first (primary) expansion
                self._abbreviations[abbrev.lower()] = synonyms[0] if synonyms else abbrev
                # Also map all synonyms back to the abbreviation
                for syn in synonyms:
                    self._term_to_canonical[syn.lower()] = abbrev

        # Common misspellings
        if 'common_misspellings' in self.synonym_groups:
            mappings = self.synonym_groups['common_misspellings'].get('mappings', {})
            for correct, misspellings in mappings.items():
                for misspelled in misspellings:
                    self._misspellings[misspelled.lower()] = correct

        # Rush-specific institutional terms
        if 'rush_institution_terms' in self.synonym_groups:
            self._rush_terms = self.synonym_groups['rush_institution_terms'].get('mappings', {})

        # Hospital codes (important for emergency-related queries)
        if 'hospital_codes' in self.synonym_groups:
            mappings = self.synonym_groups['hospital_codes'].get('mappings', {})
            for code, synonyms in mappings.items():
                self._abbreviations[code.lower()] = synonyms[0] if synonyms else code

        # Software systems (Epic, Pyxis, etc.)
        if 'software_systems' in self.synonym_groups:
            mappings = self.synonym_groups['software_systems'].get('mappings', {})
            for system, synonyms in mappings.items():
                self._abbreviations[system.lower()] = synonyms[0] if synonyms else system

    def _normalize_possessives(self, query: str) -> str:
        """
        Normalize possessive forms to improve entity detection.

        Examples:
        - "RUMC's NICU" -> "RUMC NICU"
        - "Rush's policy" -> "Rush policy"
        - "nurses'" -> "nurses"
        """
        # Remove possessive 's and ' patterns
        # Pattern handles: RUMC's, Rush's, hospital's, nurses'
        normalized = re.sub(r"(\w+)'s\b", r"\1", query)
        normalized = re.sub(r"(\w+)'\b", r"\1", normalized)
        return normalized

    def _apply_compound_expansions(
        self,
        query: str,
        result: QueryExpansion
    ) -> str:
        """
        Detect compound terms and add contextual expansions.

        When two related terms appear together (e.g., NICU + pain),
        add domain-specific synonyms for better retrieval.

        Examples:
        - "NICU pain assessment" -> adds "neonatal FLACC N-PASS infant"
        - "pediatric pain policy" -> adds "PICU child Wong-Baker"
        """
        query_lower = query.lower()
        all_expansions = []
        matched = False

        for (term1, term2), expansion in COMPOUND_EXPANSIONS.items():
            if term1 in query_lower and term2 in query_lower:
                result.expansions_applied.append({
                    'compound': f"{term1}+{term2}",
                    'expansion': expansion
                })
                logger.info(f"Compound expansion: {term1}+{term2} -> {expansion}")
                all_expansions.append(expansion)
                matched = True

        if matched:
            # Combine all expansions, deduplicating terms
            combined_terms = set()
            for exp in all_expansions:
                combined_terms.update(exp.split())
            # Remove terms already in query
            new_terms = [t for t in combined_terms if t.lower() not in query_lower]
            if new_terms:
                return f"{query} {' '.join(new_terms)}", True
            return query, True  # Matched but no new terms to add

        return query, False  # No compound match

    def _apply_single_term_expansions(
        self,
        query: str,
        result: QueryExpansion
    ) -> str:
        """
        Apply single-term expansions for key clinical terms.

        This catches queries like "neonatal pain policy" where the user
        says "neonatal" but the policy title uses "Neonatal ICU".

        Only applies if the expansion term isn't already in the query.
        """
        query_lower = query.lower()
        additions = []

        for term, expansion in SINGLE_TERM_EXPANSIONS.items():
            if term in query_lower:
                # Only add terms not already present
                new_terms = []
                for exp_word in expansion.split():
                    if exp_word.lower() not in query_lower:
                        new_terms.append(exp_word)

                if new_terms:
                    addition = ' '.join(new_terms[:4])  # Limit to 4 new terms
                    additions.append(addition)
                    result.expansions_applied.append({
                        'single_term': term,
                        'expansion': addition
                    })
                    logger.info(f"Single-term expansion: {term} -> {addition}")

        if additions:
            return f"{query} {' '.join(additions)}"
        return query

    def expand_query(
        self,
        query: str,
        max_expansions: int = 3,
        max_expansion_ratio: float = 2.0
    ) -> QueryExpansion:
        """
        Expand a user query with synonyms and corrections.

        Strategy:
        1. Correct misspellings first
        2. Expand medical abbreviations
        3. Add Rush-specific alternatives
        4. Apply pattern-based expansion rules
        5. Add domain context for short acronym-only queries
        6. NEW: Apply 2x expansion limit to prevent embedding dilution

        Args:
            query: Original user query
            max_expansions: Maximum synonym expansions per term
            max_expansion_ratio: Maximum ratio of expanded to original word count (default 2.0)

        Returns:
            QueryExpansion with original and expanded query
        """
        result = QueryExpansion(
            original_query=query,
            expanded_query=query
        )

        # Step 0: Normalize possessives first (RUMC's -> RUMC)
        query = self._normalize_possessives(query)

        # Calculate max words allowed (minimum 6 to handle short queries)
        original_word_count = len(query.split())
        max_words = max(6, int(original_word_count * max_expansion_ratio))

        words = query.split()
        expanded_words = []

        for word in words:
            word_lower = word.lower().strip('.,?!')
            expanded = word

            # 1. Correct misspellings
            if word_lower in self._misspellings:
                corrected = self._misspellings[word_lower]
                result.misspellings_corrected.append({
                    'original': word,
                    'corrected': corrected
                })
                expanded = corrected
                word_lower = corrected.lower()

            # 2. Expand abbreviations (keep original + add expansion)
            # Skip common English words that happen to match abbreviations
            # e.g., "it" should NOT become "information technology"
            ABBREVIATION_STOPWORDS = {
                'it', 'is', 'in', 'at', 'as', 'or', 'an', 'am', 'be', 'do', 'go',
                'he', 'me', 'my', 'no', 'of', 'on', 'so', 'to', 'up', 'us', 'we',
                'by', 'if', 'ms', 'mr', 'vs', 'pm', 'am'
            }
            if word_lower in self._abbreviations and word_lower not in ABBREVIATION_STOPWORDS:
                expansion = self._abbreviations[word_lower]
                result.abbreviations_expanded.append({
                    'abbreviation': word,
                    'expansion': expansion
                })
                # Keep both terms space-separated for better semantic/vector matching
                # (parentheses can confuse embeddings)
                expanded = f"{word} {expansion}"

            expanded_words.append(expanded)

        # Reconstruct query
        expanded_query = ' '.join(expanded_words)

        # 3. Apply pattern-based expansion rules
        expanded_query = self._apply_expansion_rules(query, expanded_query, result)

        # 4. Handle multi-word Rush terms (e.g., "code blue", "labor and delivery")
        expanded_query = self._expand_multiword_terms(query, expanded_query, result)

        # 5. NEW: Add domain context for short acronym-only queries
        # This helps queries like "SBAR" find the same results as "SBAR communication framework"
        expanded_query = self._add_context_for_short_queries(query, expanded_query, result)

        # 6. Apply compound term expansions (NICU + pain -> neonatal terms)
        expanded_query, compound_matched = self._apply_compound_expansions(expanded_query, result)

        # 6.5 Apply single-term expansions if no compound match
        # This catches "neonatal pain" -> adds "Neonatal ICU" even without compound
        if not compound_matched:
            expanded_query = self._apply_single_term_expansions(expanded_query, result)

        # 7. Truncate if over limit to prevent embedding dilution
        # Research shows over-expansion causes semantic drift in embeddings
        expanded_words_final = expanded_query.split()
        if len(expanded_words_final) > max_words:
            expanded_query = ' '.join(expanded_words_final[:max_words])
            logger.info(f"Truncated expansion: {len(expanded_words_final)} -> {max_words} words")

        result.expanded_query = expanded_query

        if result.expansions_applied or result.misspellings_corrected or result.abbreviations_expanded:
            logger.info(f"Query expansion: '{query}' → '{expanded_query}'")

        return result

    def _add_context_for_short_queries(
        self,
        original: str,
        current: str,
        result: QueryExpansion
    ) -> str:
        """
        Add domain context for short acronym-only queries.
        
        Short queries like "SBAR" or "RRT" often miss relevant documents because
        they lack context. This adds policy-related terms to improve retrieval.
        
        Examples:
        - "SBAR" -> "SBAR situation background assessment recommendation communication hand-off"
        - "RRT" -> "RRT rapid response team emergency"
        """
        words = original.split()
        
        # Only apply to very short queries (1-2 words)
        if len(words) > 2:
            return current
        
        # Domain-specific context additions for common healthcare acronyms
        # CONSERVATIVE: Max 5 terms per entry to prevent embedding dilution
        # Research shows over-expansion causes semantic drift in embeddings
        context_map = {
            # Communication (fix gen-004, gen-006) - SBAR = Situation Background Assessment Recommendation
            'sbar': 'Situation Background Assessment Recommendation handoff',
            'shift': 'shift change handoff report',
            'handoff': 'hand-off communication report',
            'hand-off': 'handoff communication report',
            'report': 'shift handoff SBAR communication',

            # Rapid Response (fix multi-001, adv-003)
            'rrt': 'rapid response team family',
            'rapid': 'rapid response RRT',

            # Verbal Orders (fix edge-001)
            'verbal': 'verbal telephone orders',
            'orders': 'verbal telephone orders',

            # Latex/Safety (fix edge-008, multi-002)
            'latex': 'latex allergy product precautions',
            'product': 'product latex identification labeling',
            'allergy': 'allergy latex precautions',
            'patient': 'patient identification safety',
            'identification': 'identification patient safety',
            'safety': 'safety patient precautions',

            # Epic/Documentation (fix multi-003)
            'epic': 'epic EHR documentation charting',
            'documentation': 'documentation Epic charting',

            # Language Services (fix retrieval for translator)
            'translator': 'interpreter language services translation',
            'interpreter': 'translator language services translation',

            # Clinical Colloquialisms & Brands (Deep semantic audit)
            'sitter': 'safety assistant patient observer suicide precautions',
            'vac': 'negative pressure wound therapy NPWT vacuum',
            'woundvac': 'negative pressure wound therapy NPWT',
            'ceribell': 'rapid EEG seizure monitoring',
            'veletri': 'epoprostenol prostacyclin',
            'remodulin': 'treprostinil prostacyclin',
            'licox': 'brain oxygen monitoring cerebral oxygenation',
            'vashe': 'hypochlorous acid wound cleanser',
            'shingrix': 'zoster vaccine shingles',
            'cholestech': 'lipid profile cholesterol POCT',
            'glucostabilizer': 'insulin drip infusion glycemic',
            'agiloft': 'contract management system CMS',
            'kronos': 'timekeeping time attendance',
            'vocera': 'communication badge hands-free',
            'flexing': 'reallocation nursing staffing floating',
            'firewatch': 'impairment fire protection downtime',

            # Standard acronyms (conservative - 3-4 terms)
            'rn': 'registered nurse nursing',
            'icu': 'intensive care critical',
            'ed': 'emergency department ER',
            'cpr': 'resuscitation cardiac arrest',
            'dnr': 'do not resuscitate',
            'hipaa': 'privacy patient information',
            'pca': 'patient controlled analgesia',
            'picc': 'central catheter line',
            'npo': 'nothing by mouth fasting',
            'prn': 'as needed medication',
            'stat': 'immediately urgent',
            'vte': 'blood clot prevention',
            'fall': 'fall prevention risk',
            'ai': 'artificial intelligence technology',
        }
        
        expanded = current
        for word in words:
            word_lower = word.lower()
            if word_lower in context_map:
                context_terms = context_map[word_lower]
                expanded = f"{expanded} {context_terms}"
                result.expansions_applied.append({
                    'term': word,
                    'context_added': context_terms
                })
                logger.debug(f"Added context for '{word}': {context_terms}")
        
        return expanded

    def _apply_expansion_rules(
        self,
        original: str,
        current: str,
        result: QueryExpansion
    ) -> str:
        """Apply pattern-based query expansion rules."""
        expanded = current

        for rule in self.query_expansion_rules:
            pattern = rule.get('pattern', '')
            expand_with = rule.get('expand_with', [])

            if not pattern or not expand_with:
                continue

            try:
                if re.match(pattern, original, re.IGNORECASE):
                    # Add expansion keywords to query
                    additions = ' '.join(expand_with[:2])  # Limit to top 2
                    expanded = f"{expanded} {additions}"
                    result.expansions_applied.append({
                        'pattern': pattern,
                        'additions': expand_with[:2]
                    })
                    break  # Apply only first matching rule
            except re.error:
                continue

        return expanded

    def _expand_multiword_terms(
        self,
        original: str,
        current: str,
        result: QueryExpansion
    ) -> str:
        """Expand multi-word terms like 'code blue', 'labor and delivery'."""
        expanded = current
        original_lower = original.lower()

        # Hospital codes (multi-word)
        if 'hospital_codes' in self.synonym_groups:
            for code, synonyms in self.synonym_groups['hospital_codes'].get('mappings', {}).items():
                if code.lower() in original_lower:
                    # Add primary synonym
                    if synonyms:
                        expanded = f"{expanded} {synonyms[0]}"
                        result.expansions_applied.append({
                            'term': code,
                            'expansion': synonyms[0]
                        })
                    break

        # Department/unit names
        if 'departments_units' in self.synonym_groups:
            for dept, synonyms in self.synonym_groups['departments_units'].get('mappings', {}).items():
                if dept.lower() in original_lower:
                    # Add abbreviation if available
                    abbrev = next((s for s in synonyms if s.isupper() and len(s) <= 4), None)
                    if abbrev:
                        expanded = f"{expanded} {abbrev}"
                        result.expansions_applied.append({
                            'term': dept,
                            'expansion': abbrev
                        })
                    break

        # Communication terminology (fix gen-006: "shift report" → "hand-off")
        # The policy uses "Hand Off Communication" but users search for "shift report"
        communication_terms = {
            'shift report': 'hand-off handoff communication nursing',
            'change of shift': 'hand-off handoff patient status',
            'shift change': 'hand-off handoff communication',
            'bedside report': 'hand-off handoff nursing communication',
            'nursing report': 'hand-off handoff shift communication',
        }
        for term, expansion in communication_terms.items():
            if term in original_lower:
                expanded = f"{expanded} {expansion}"
                result.expansions_applied.append({
                    'term': term,
                    'expansion': expansion
                })
                logger.debug(f"Multi-word expansion: '{term}' → '{expansion}'")
                break  # Only apply first match

        return expanded

    def get_abbreviation_context(self, limit: int = 50) -> str:
        """
        Generate a context string of key abbreviations for the agent prompt.

        This helps the agent understand common abbreviations users might use.
        """
        if not self._abbreviations:
            return ""

        # Prioritize most common/important abbreviations
        priority_abbrevs = [
            'ED', 'ICU', 'NICU', 'PICU', 'OR', 'PACU', 'L&D', 'LD',
            'DNR', 'HIPAA', 'EMTALA', 'PPE', 'NPO', 'PRN', 'STAT',
            'IV', 'CVC', 'PICC', 'CPR', 'BLS', 'AED',
            'RUMC', 'RUMG', 'ROPH', 'RUSH', 'RCMC',
            'EPIC', 'PYXIS', 'WORKDAY'
        ]

        context_lines = ["Key abbreviations:"]
        added = 0

        # Add priority abbreviations first
        for abbrev in priority_abbrevs:
            if abbrev.lower() in self._abbreviations and added < limit:
                expansion = self._abbreviations[abbrev.lower()]
                context_lines.append(f"- {abbrev}: {expansion}")
                added += 1

        return '\n'.join(context_lines)

    def get_rush_terms_context(self) -> str:
        """Generate context about Rush-specific institutional terms."""
        if not self._rush_terms:
            return ""

        lines = ["Rush University System for Health locations and terms:"]
        for term, synonyms in list(self._rush_terms.items())[:10]:
            lines.append(f"- {term}: {', '.join(synonyms[:3])}")

        return '\n'.join(lines)

    def correct_misspelling(self, word: str) -> Optional[str]:
        """Check and correct a single word's spelling."""
        return self._misspellings.get(word.lower())

    def expand_abbreviation(self, abbrev: str) -> Optional[str]:
        """Get the expansion of an abbreviation."""
        return self._abbreviations.get(abbrev.lower())

    def get_synonyms_for_term(self, term: str, category: Optional[str] = None) -> List[str]:
        """Get all synonyms for a term, optionally filtered by category."""
        term_lower = term.lower()
        synonyms = []

        groups_to_search = [category] if category else self.synonym_groups.keys()

        for group_name in groups_to_search:
            if group_name not in self.synonym_groups:
                continue

            mappings = self.synonym_groups[group_name].get('mappings', {})

            # Check if term is a key
            if term_lower in {k.lower() for k in mappings.keys()}:
                for key, syns in mappings.items():
                    if key.lower() == term_lower:
                        synonyms.extend(syns)
                        break

            # Check if term is in any synonym list
            for key, syns in mappings.items():
                if term_lower in [s.lower() for s in syns]:
                    synonyms.append(key)
                    synonyms.extend(s for s in syns if s.lower() != term_lower)
                    break

        return list(set(synonyms))  # Deduplicate


# Global singleton instance
_synonym_service: Optional[SynonymService] = None


def get_synonym_service() -> SynonymService:
    """Get or create the global synonym service instance."""
    global _synonym_service
    if _synonym_service is None:
        _synonym_service = SynonymService()
    return _synonym_service
