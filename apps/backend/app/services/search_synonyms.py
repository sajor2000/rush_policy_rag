"""
Healthcare synonym mappings for Azure AI Search.

This module contains the SYNONYMS constant used for index-time synonym expansion
in Azure AI Search. These synonyms help match different medical terminology
variations used by clinicians.

Extracted from azure_policy_index.py as part of tech debt refactoring.

Categories:
- Departments & Units (18 rules)
- Emergency Codes (12 rules)
- Clinical Procedures (13 rules)
- Patient Safety (10 rules)
- Infection Control (10 rules)
- Rush Institution (9 rules)
- Compliance (8 rules)
- Staff Roles (8 rules)
- Equipment (7 rules)
- Medications (7 rules)
- And more...
"""

# Azure AI Search synonym map name
SYNONYM_MAP_NAME = "healthcare-synonyms"

# Comprehensive healthcare synonyms for Azure AI Search
# Format: Apache Solr synonym format
# - "a, b, c" means all terms are equivalent (bidirectional)
# - "a => b" means 'a' is replaced with 'b' (unidirectional)
SYNONYMS = """
# === DEPARTMENTS & UNITS ===
# Emergency department variations
ed, er, emergency department, emergency room, emergency services

# Intensive care unit variations
icu, intensive care unit, critical care unit, ccu

# Operating room variations
or, operating room, surgery, surgical suite, surgical services

# Labor and delivery
l&d, labor and delivery, ob, obstetrics, maternity

# Post-anesthesia care unit
pacu, post-anesthesia care unit, recovery room

# Telemetry
tele, telemetry, cardiac monitoring unit

# Medical-surgical units
med-surg, medical-surgical, medsurg, medical surgical

# Outpatient services
op, outpatient, ambulatory, clinic

# Radiology
radiology, diagnostic imaging, imaging services, x-ray

# Pharmacy
pharmacy, pharmaceutical services, medication dispensing

# Laboratory
lab, laboratory, clinical laboratory, pathology

# Respiratory
rt, respiratory therapy, respiratory services, pulmonary services

# Physical therapy
pt, physical therapy, physiotherapy, rehabilitation

# Occupational therapy
ot, occupational therapy

# Case management
cm, case management, care coordination, discharge planning

# Social work
sw, social work, social services

# Chaplain services
chaplain, spiritual care, pastoral care

# === EMERGENCY CODES ===
# Cardiac arrest codes
code blue, cardiac arrest, cardiopulmonary arrest, cpr

# Rapid response
rrt, rapid response, rapid response team, medical emergency team, met

# Fire codes
code red, fire, fire emergency

# Security codes
code gray, combative patient, security emergency, violent patient

# Missing patient
code yellow, missing patient, elopement, patient elopement

# Bomb threat
code black, bomb threat

# Hazmat
code orange, hazmat, hazardous materials, chemical spill

# Weather emergency
code white, weather emergency, severe weather

# Mass casualty
mci, mass casualty, mass casualty incident, disaster

# Active shooter
code silver, active shooter, armed intruder

# Child abduction
code pink, infant abduction, child abduction, baby abduction

# Stroke alert
stroke alert, code stroke, brain attack

# === CLINICAL PROCEDURES ===
# Intubation
intubation, ett placement, endotracheal tube, airway management

# IV access
iv, intravenous, iv access, peripheral line, peripheral iv

# Central line
central line, cvc, central venous catheter, picc, picc line

# Foley catheter
foley, urinary catheter, indwelling catheter, bladder catheter

# Nasogastric tube
ng tube, nasogastric tube, feeding tube, orogastric tube

# Blood transfusion
transfusion, blood transfusion, blood products, prbc

# Medication administration
med admin, medication administration, drug administration

# Wound care
wound care, dressing change, wound management

# Pain management
pain management, analgesia, pain control

# Sedation
sedation, conscious sedation, moderate sedation, procedural sedation

# Restraints
restraints, patient restraints, physical restraints, soft restraints

# Isolation
isolation, isolation precautions, contact isolation, droplet isolation, airborne isolation

# Suctioning
suction, suctioning, airway suctioning, oral suctioning

# === PATIENT SAFETY ===
# Fall prevention
fall prevention, fall risk, fall precautions, bed alarm

# Pressure ulcer prevention
pressure ulcer, decubitus, bedsore, skin integrity, pressure injury

# Patient identification
patient id, patient identification, two patient identifiers, armband

# Medication reconciliation
med rec, medication reconciliation, medication review

# Handoff communication
handoff, hand-off, sbar, shift report, patient handoff

# Time out
time out, surgical time out, universal protocol, surgical pause

# Suicide precautions
suicide precautions, si precautions, 1:1 observation, constant observation

# Elopement risk
elopement, wandering, flight risk

# Aspiration precautions
aspiration precautions, aspiration risk, swallow precautions, dysphagia precautions

# Bleeding precautions
bleeding precautions, anticoagulation, fall risk with bleeding

# === INFECTION CONTROL ===
# Hand hygiene
hand hygiene, handwashing, hand washing, hand sanitizer

# Personal protective equipment
ppe, personal protective equipment, gown and gloves, isolation gear

# Standard precautions
standard precautions, universal precautions, body fluid precautions

# Contact precautions
contact precautions, contact isolation, mrsa precautions

# Droplet precautions
droplet precautions, droplet isolation, flu precautions

# Airborne precautions
airborne precautions, airborne isolation, tb precautions, n95

# Isolation gown
isolation gown, protective gown, yellow gown

# Sterile technique
sterile technique, aseptic technique, sterile field

# Disinfection
disinfection, terminal cleaning, room cleaning, surface disinfection

# Sharps safety
sharps, needle safety, sharps disposal, needle stick prevention

# === RUSH INSTITUTION CODES ===
# Rush University Medical Center
rumc, rush university medical center, rush medical center, rush hospital

# Rush University Medical Group
rumg, rush university medical group

# Rush Medical Group
rmg, rush medical group

# Rush Oak Park Hospital
roph, rush oak park, oak park hospital, oak park campus

# Rush Copley Medical Center
rcmc, rush copley, copley medical center, copley hospital

# Rush Children's Hospital
rch, rush children, pediatric hospital, childrens hospital

# Rush Oak Park Physicians Group
roppg, oak park physicians

# Rush Copley Medical Group
rcmg, copley medical group

# Rush University
ru, rush university

# === COMPLIANCE & REGULATORY ===
# HIPAA
hipaa, patient privacy, phi, protected health information, health information privacy

# Joint Commission
joint commission, jcaho, tjc, accreditation

# CMS
cms, centers for medicare, medicare, medicaid

# OSHA
osha, workplace safety, occupational safety

# EMTALA
emtala, emergency treatment, medical screening exam

# ADA
ada, americans with disabilities, disability accommodation

# Informed consent
informed consent, consent form, surgical consent, procedure consent

# Advance directive
advance directive, living will, healthcare proxy, durable power of attorney, dpoa

# === STAFF ROLES ===
# Registered nurse
rn, registered nurse, staff nurse, bedside nurse

# Licensed practical nurse
lpn, licensed practical nurse, lvn, licensed vocational nurse

# Certified nursing assistant
cna, certified nursing assistant, nurse aide, patient care tech, pct

# Physician
md, do, physician, doctor, attending, hospitalist

# Nurse practitioner
np, nurse practitioner, aprn, advanced practice nurse

# Physician assistant
pa, physician assistant, pa-c

# Charge nurse
charge nurse, charge rn, shift supervisor

# House supervisor
house supervisor, nursing supervisor, administrative supervisor

# === EQUIPMENT & DEVICES ===
# Ventilator
ventilator, vent, breathing machine, mechanical ventilation

# Cardiac monitor
cardiac monitor, heart monitor, telemetry monitor, bedside monitor

# Infusion pump
infusion pump, iv pump, medication pump, pca pump

# Defibrillator
defibrillator, aed, automated external defibrillator, shock

# Pulse oximeter
pulse ox, pulse oximeter, oxygen saturation, spo2

# Blood pressure monitor
bp cuff, blood pressure cuff, sphygmomanometer, nibp

# Glucometer
glucometer, blood glucose, fingerstick, blood sugar

# === MEDICATIONS ===
# Pain medications
narcotic, opioid, controlled substance, pain medication, analgesic

# Blood thinners
anticoagulant, blood thinner, heparin, coumadin, warfarin, lovenox

# Antibiotics
antibiotic, antimicrobial, anti-infective

# Insulin
insulin, diabetes medication, blood sugar medication

# Sedatives
sedative, anxiolytic, benzodiazepine, benzo

# Vasopressors
vasopressor, pressor, levophed, norepinephrine, dopamine

# Diuretics
diuretic, water pill, lasix, furosemide

# === DOCUMENTATION ===
# Electronic health record
ehr, emr, electronic health record, electronic medical record, epic, cerner

# Nursing notes
nursing notes, progress notes, documentation, charting

# Medication administration record
mar, medication administration record, emar

# Care plan
care plan, plan of care, nursing care plan

# Assessment
assessment, nursing assessment, patient assessment, admission assessment

# === CLINICAL CONDITIONS ===
# Diabetes
diabetes, dm, diabetic, blood sugar problem

# Hypertension
hypertension, htn, high blood pressure, elevated bp

# Heart failure
heart failure, chf, congestive heart failure, hf

# Stroke
stroke, cva, cerebrovascular accident, brain attack

# Sepsis
sepsis, septic, severe infection, systemic infection

# Pneumonia
pneumonia, pna, lung infection

# Deep vein thrombosis
dvt, deep vein thrombosis, blood clot, venous thrombosis

# Pulmonary embolism
pe, pulmonary embolism, lung clot

# Acute kidney injury
aki, acute kidney injury, renal failure, kidney failure

# Myocardial infarction
mi, myocardial infarction, heart attack, stemi, nstemi

# === MISCELLANEOUS HEALTHCARE ===
# Vital signs
vitals, vital signs, vs, blood pressure, pulse, temperature, respirations

# Activities of daily living
adl, activities of daily living, self-care

# Nothing by mouth
npo, nothing by mouth, nil per os

# As needed
prn, as needed, pro re nata

# Every shift
q shift, every shift, each shift

# Immediately
stat, immediately, emergent, urgent

# Discharge
discharge, dc, d/c, release, going home

# Admission
admission, admit, inpatient admission
"""


def get_synonym_rules() -> list[str]:
    """
    Parse SYNONYMS constant and return list of rules.

    Filters out comments and empty lines.

    Returns:
        List of synonym rules in Apache Solr format
    """
    rules = []
    for line in SYNONYMS.strip().split('\n'):
        line = line.strip()
        if line and not line.startswith('#'):
            rules.append(line)
    return rules


def get_synonyms_text() -> str:
    """
    Get cleaned synonym text for Azure AI Search synonym map.

    Returns:
        Newline-separated synonym rules without comments
    """
    return '\n'.join(get_synonym_rules())
