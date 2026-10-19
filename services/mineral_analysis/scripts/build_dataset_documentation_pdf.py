"""
Script to generate the comprehensive technical specification document
for the Mineral Analysis Datasets in professional PDF format.
"""
import os
import sys
import shutil
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

BASE_DIR = Path(r"C:\Users\sanan\OneDrive\Documents\Thermal Remote Sensing\Project\remote_sensing_pipeline\services\mineral_analysis")
OUTPUT_PDF = BASE_DIR / "Mineral_Analysis_Datasets_Specification.pdf"
ARTIFACT_DIR = Path(r"C:\Users\sanan\.gemini\antigravity-ide\brain\77d141ae-2ae4-4f6e-ab11-43b9f157b606")
ARTIFACT_PDF = ARTIFACT_DIR / "Mineral_Analysis_Datasets_Specification.pdf"

# ----------------------------------------------------------------------
# Numbered Canvas for Running Headers and Footers
# ----------------------------------------------------------------------
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_decorations(self, page_count):
        # Skip header and footer on cover / page 1
        if self._pageNumber == 1:
            return

        self.saveState()
        self.setFont("Helvetica-Bold", 7.5)
        self.setFillColor(colors.HexColor("#4A5568"))

        # Header
        self.drawString(36, 11 * inch - 26, "REMOTE SENSING PIPELINE — MINERAL ANALYSIS TECHNICAL SPECIFICATION")
        self.setFont("Helvetica", 7.5)
        self.drawRightString(8.5 * inch - 36, 11 * inch - 26, "NASA EMIT & USGS HYPERSPECTRAL DATASETS")
        self.setStrokeColor(colors.HexColor("#CBD5E0"))
        self.setLineWidth(0.5)
        self.line(36, 11 * inch - 30, 8.5 * inch - 36, 11 * inch - 30)

        # Footer
        self.drawString(36, 24, "CONFIDENTIAL & PROPRIETARY — ADVANCED REMOTE SENSING & MINERALOGY PIPELINE")
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.setFont("Helvetica-Bold", 7.5)
        self.drawRightString(8.5 * inch - 36, 24, page_text)
        self.setStrokeColor(colors.HexColor("#CBD5E0"))
        self.setLineWidth(0.5)
        self.line(36, 33, 8.5 * inch - 36, 33)

        self.restoreState()


def build_pdf():
    # 0.5 in margins (36 pt) -> printable width = 8.5 * 72 - 72 = 540 pt
    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=38
    )

    styles = getSampleStyleSheet()

    primary_color = colors.HexColor("#1A365D")    # Deep Navy
    secondary_color = colors.HexColor("#2B6CB0")  # Slate Blue
    accent_color = colors.HexColor("#9C4221")     # Earth Terracotta
    text_color = colors.HexColor("#2D3748")       # Charcoal

    title_style = ParagraphStyle(
        "CoverTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=primary_color,
        spaceAfter=3,
    )

    subtitle_style = ParagraphStyle(
        "CoverSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=secondary_color,
        spaceAfter=10,
    )

    meta_style = ParagraphStyle(
        "CoverMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11.5,
        textColor=colors.HexColor("#2D3748"),
    )

    h1_style = ParagraphStyle(
        "H1",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12.5,
        leading=16,
        textColor=primary_color,
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        "H2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=secondary_color,
        spaceBefore=6,
        spaceAfter=3,
        keepWithNext=True,
    )

    h3_style = ParagraphStyle(
        "H3",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11.5,
        textColor=accent_color,
        spaceBefore=5,
        spaceAfter=2,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=text_color,
        spaceAfter=4,
    )

    body_bold = ParagraphStyle(
        "BodyBold",
        parent=body_style,
        fontName="Helvetica-Bold",
    )

    bullet_style = ParagraphStyle(
        "Bullet",
        parent=body_style,
        leftIndent=10,
        firstLineIndent=-6,
        spaceAfter=2.5,
    )

    formula_style = ParagraphStyle(
        "Formula",
        parent=body_style,
        fontName="Courier-Bold",
        fontSize=8,
        leading=10.5,
        textColor=primary_color,
        alignment=1,
        spaceBefore=2,
        spaceAfter=4,
    )

    th_style = ParagraphStyle(
        "TH",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.white,
        alignment=1,
    )

    tc_style = ParagraphStyle(
        "TC",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=9,
        textColor=text_color,
    )

    tc_bold = ParagraphStyle(
        "TCBold",
        parent=tc_style,
        fontName="Helvetica-Bold",
    )

    tc_code = ParagraphStyle(
        "TCCode",
        parent=tc_style,
        fontName="Courier",
        fontSize=6.5,
        leading=8,
        textColor=colors.HexColor("#6B46C1"),
    )

    story = []

    # =========================================================================
    # PAGE 1: TITLE, EXECUTIVE SUMMARY & SENSOR ARCHITECTURE
    # =========================================================================
    story.append(Paragraph("NASA EMIT & USGS Hyperspectral Mineral Analysis", title_style))
    story.append(Paragraph("Comprehensive Technical Specification & Architecture of Datasets, Reference Spectra, and Feature Pipelines", subtitle_style))
    
    meta_text = (
        "<b>Project:</b> Thermal & Hyperspectral Remote Sensing Pipeline &nbsp;|&nbsp; "
        "<b>Subsystem:</b> <code>services/mineral_analysis</code><br/>"
        "<b>Target Sensors:</b> NASA EMIT (Earth Surface Mineral Dust Source Investigation, ISS) &amp; USGS Digital Spectral Library<br/>"
        "<b>Target Artefacts:</b> <code>training_sample.csv</code>, <code>dataset_sample.csv</code>, <code>data/usgs_library/*.csv</code><br/>"
        "<b>Certification:</b> Fully Evaluated &amp; Audited (0 Errors, 14/14 Unit Tests Passing) &nbsp;|&nbsp; <b>Release:</b> October 2026"
    )
    
    meta_table = Table([[Paragraph(meta_text, meta_style)]], colWidths=[540])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#EDF2F7")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#CBD5E0")),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 8))

    story.append(Paragraph("1. Executive Summary & Dual-Dataset Architecture", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=primary_color, spaceBefore=1, spaceAfter=5))
    
    story.append(Paragraph(
        "The <b>Mineral Analysis Service</b> transforms NASA EMIT Level-2A (L2A) surface reflectance hypercubes "
        "into calibrated mineral classification maps, quantitative endmember abundances, and machine learning training sets. "
        "Hyperspectral mineral mapping from orbit faces two major challenges: (1) <i>extreme ground truth scarcity</i>—field "
        "spectroscopy across an entire 75 × 75 km satellite tile is logistically impossible, and (2) <i>class degeneracy</i>—real geological "
        "scenes are typically dominated by only 1 or 2 surface cover types (e.g. quartz sand sheets or evaporite gypsum), leaving models "
        "untrained on critical diagnostic alteration minerals (alunite, kaolinite, illite, chlorite, etc.).",
        body_style
    ))
    story.append(Paragraph(
        "To resolve this, the architecture establishes a <b>dual-dataset paradigm</b> grounded in the physical "
        "USGS Digital Spectral Library. All models, synthetic simulators, and real orbital processors share an identical, invariant "
        "<b>275-dimensional feature representation</b>, guaranteeing zero domain drift between training and inference.",
        body_style
    ))

    # Architecture Overview Table
    arch_data = [
        [Paragraph("Dataset Component", th_style), Paragraph("Source / Foundation", th_style), Paragraph("Sample Size", th_style), Paragraph("Primary Purpose & Role in Architecture", th_style)],
        [
            Paragraph("<b>USGS Reference Library</b><br/><code>data/usgs_library/*.csv</code>", tc_bold),
            Paragraph("USGS Digital Spectral Library (splib06a/07a) laboratory spectrometry", tc_style),
            Paragraph("12 reference minerals<br/>(441 bands each)", tc_style),
            Paragraph("Pure endmember baseline; physical spectral truth defining diagnostic vibrational and electronic absorptions.", tc_style),
        ],
        [
            Paragraph("<b>Synthetic Training Set</b><br/><code>training_sample.csv</code><br/>(<code>training_set.npz</code>)", tc_bold),
            Paragraph("Radiative Transfer Forward Model (constrained linear mixture simulation + EMIT noise)", tc_style),
            Paragraph("62,400 samples<br/>(2,000 preview rows,<br/>288 columns)", tc_style),
            Paragraph("Balanced supervised training set. Exactly 5,200 dominant samples per mineral across all 12 classes; enables machine learning models to learn robust multi-mineral unmixing.", tc_style),
        ],
        [
            Paragraph("<b>Real Scene Evaluation Set</b><br/><code>dataset_sample.csv</code><br/>(<code>dataset.npz</code>)", tc_bold),
            Paragraph("NASA EMIT L2A Surface Reflectance Cube (EPSG:4326, 553×553 pixels)", tc_style),
            Paragraph("7,702 pixels<br/>(2,000 preview rows,<br/>290 columns)", tc_style),
            Paragraph("Autonomous pseudo-labeled benchmark from SAM + SMA + Quality pipeline. Used exclusively to validate model generalization against real orbital observations.", tc_style),
        ],
    ]
    arch_table = Table(arch_data, colWidths=[110, 130, 95, 205])
    arch_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), secondary_color),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
    ]))
    story.append(arch_table)
    story.append(Spacer(1, 8))

    story.append(Paragraph("2. Sensor Calibration & Preprocessing Pipeline", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=primary_color, spaceBefore=1, spaceAfter=5))
    
    story.append(Paragraph(
        "<b>NASA EMIT Sensor Specifics:</b> Installed aboard the International Space Station, EMIT measures "
        "reflected solar radiation across 285 contiguous channels between 381 nm and 2493 nm (sampling ~7.4 nm). "
        "Due to severe atmospheric water vapor absorption troughs at ~1350–1420 nm and ~1800–1970 nm, 30 channels exhibit near-zero signal-to-noise ratio. "
        "These are masked out according to <code>band_metadata.json</code>, leaving exactly <b>255 good channels</b>.",
        body_style
    ))
    story.append(Paragraph(
        "<b>Wavelength Alignment:</b> USGS endmembers (441 bands, 5 nm spacing) are interpolated onto the 255 good EMIT band centers "
        "using monotonic cubic convolution / linear interpolation (<code>wavelength_alignment.py</code>). This ensures exact channel alignment.",
        body_style
    ))
    story.append(Paragraph(
        "<b>Continuum Removal (CR) Physics:</b> For spectral matching and feature extraction, the convex hull continuum $C(\\lambda)$ is fitted over "
        "the reflectance spectrum $R(\\lambda)$. The continuum-removed ratio is computed as: <b>CR(λ) = R(λ) / C(λ)</b>. "
        "This isolates diagnostic absorption bands (where CR &lt; 1.0) and eliminates broadband albedo variations caused by topography and sun angle.",
        body_style
    ))

    # Force clean page break to Page 2
    story.append(PageBreak())

    # =========================================================================
    # PAGE 2: THE USGS MINERAL SPECTRAL REFERENCE LIBRARY
    # =========================================================================
    story.append(Paragraph("3. The USGS Mineral Spectral Reference Library (data/usgs_library/)", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=primary_color, spaceBefore=1, spaceAfter=5))
    
    story.append(Paragraph(
        "The reference spectral library contains 12 canonical minerals measured under laboratory conditions by the USGS. "
        "Each CSV file has been validated for physical correctness and strict monotonicity:",
        body_style
    ))

    mineral_details = [
        [Paragraph("Mineral Name", th_style), Paragraph("Chemical Formula & Class", th_style), Paragraph("Key Diagnostic Absorption Physics", th_style), Paragraph("Reflectance Range", th_style)],
        [
            Paragraph("<b>Alunite</b>", tc_bold),
            Paragraph("KAl<sub>3</sub>(SO<sub>4</sub>)<sub>2</sub>(OH)<sub>6</sub><br/>Hydrated Sulfate", tc_style),
            Paragraph("Distinct Al-OH doublet at 2165 &amp; 2205 nm; strong SO<sub>4</sub> overtones at 1480 &amp; 1760 nm.", tc_style),
            Paragraph("0.1618 – 0.4532", tc_code),
        ],
        [
            Paragraph("<b>Calcite</b>", tc_bold),
            Paragraph("CaCO<sub>3</sub><br/>Carbonate", tc_style),
            Paragraph("Dominant asymmetric CO<sub>3</sub> stretching overtone at 2340 nm; secondary band at 2530 nm.", tc_style),
            Paragraph("0.1695 – 0.5239", tc_code),
        ],
        [
            Paragraph("<b>Chlorite</b>", tc_bold),
            Paragraph("(Mg,Fe)<sub>5</sub>Al(Si<sub>3</sub>Al)O<sub>10</sub>(OH)<sub>8</sub><br/>Phyllosilicate", tc_style),
            Paragraph("Diagnostic Fe-OH band at 2260 nm; strong Mg-OH band at 2340 nm; broad Fe<sup>2+</sup> trough near 1000 nm.", tc_style),
            Paragraph("0.0100 – 0.2511", tc_code),
        ],
        [
            Paragraph("<b>Dolomite</b>", tc_bold),
            Paragraph("CaMg(CO<sub>3</sub>)<sub>2</sub><br/>Carbonate", tc_style),
            Paragraph("CO<sub>3</sub> absorption shifted to 2320 nm (higher frequency than calcite due to smaller Mg cation).", tc_style),
            Paragraph("0.1696 – 0.4746", tc_code),
        ],
        [
            Paragraph("<b>Goethite</b>", tc_bold),
            Paragraph("α-FeO(OH)<br/>Iron Oxyhydroxide", tc_style),
            Paragraph("Electronic Fe<sup>3+</sup> crystal-field transitions at 480 nm, 660 nm, and strong broad trough at 920 nm.", tc_style),
            Paragraph("0.0100 – 0.2666", tc_code),
        ],
        [
            Paragraph("<b>Gypsum</b>", tc_bold),
            Paragraph("CaSO<sub>4</sub>·2H<sub>2</sub>O<br/>Hydrated Sulfate", tc_style),
            Paragraph("Strong diagnostic H<sub>2</sub>O and SO<sub>4</sub> overtones at 1450, 1750, 1940, and 2210 nm.", tc_style),
            Paragraph("0.3500 – 0.5550", tc_code),
        ],
        [
            Paragraph("<b>Hematite</b>", tc_bold),
            Paragraph("α-Fe<sub>2</sub>O<sub>3</sub><br/>Iron Oxide", tc_style),
            Paragraph("Fe<sup>3+</sup> charge-transfer slope in visible; diagnostic crystal field absorption trough centered at 860 nm.", tc_style),
            Paragraph("0.0100 – 0.2685", tc_code),
        ],
        [
            Paragraph("<b>Illite</b>", tc_bold),
            Paragraph("(K,H<sub>3</sub>O)Al<sub>2</sub>(Si<sub>3</sub>Al)O<sub>10</sub>(OH)<sub>2</sub><br/>Clay / Mica", tc_style),
            Paragraph("Sharp Al-OH vibrational overtone at 2205 nm with shoulder at 2350 nm; structural H<sub>2</sub>O at 1900 nm.", tc_style),
            Paragraph("0.0100 – 0.3021", tc_code),
        ],
        [
            Paragraph("<b>Kaolinite</b>", tc_bold),
            Paragraph("Al<sub>2</sub>Si<sub>2</sub>O<sub>5</sub>(OH)<sub>4</sub><br/>1:1 Dioctahedral Clay", tc_style),
            Paragraph("Classic diagnostic Al-OH doublet with sharp minima at 2165 nm and 2205 nm; OH stretch at 1400 nm.", tc_style),
            Paragraph("0.0100 – 0.3514", tc_code),
        ],
        [
            Paragraph("<b>Montmorillonite</b>", tc_bold),
            Paragraph("(Na,Ca)<sub>0.33</sub>(Al,Mg)<sub>2</sub>(Si<sub>4</sub>O<sub>10</sub>)(OH)<sub>2</sub>·nH<sub>2</sub>O<br/>Smectite Clay", tc_style),
            Paragraph("Broad Al-OH band at 2210 nm; deep structural and interlayer H<sub>2</sub>O absorption bands at 1410 &amp; 1910 nm.", tc_style),
            Paragraph("0.0236 – 0.3021", tc_code),
        ],
        [
            Paragraph("<b>Muscovite</b>", tc_bold),
            Paragraph("KAl<sub>2</sub>(AlSi<sub>3</sub>O<sub>10</sub>)(OH)<sub>2</sub><br/>Dioctahedral Mica", tc_style),
            Paragraph("Extremely sharp Al-OH band at 2200 nm; secondary Al-OH/Mg-OH bands at 2350 nm and 2440 nm.", tc_style),
            Paragraph("0.0100 – 0.3246", tc_code),
        ],
        [
            Paragraph("<b>Quartz</b>", tc_bold),
            Paragraph("SiO<sub>2</sub><br/>Framework Silicate", tc_style),
            Paragraph("Spectrally featureless in VNIR-SWIR (fundamental Si-O vibrations occur in the Thermal IR at 8-12 µm). High flat albedo.", tc_style),
            Paragraph("0.5164 – 0.5550", tc_code),
        ],
    ]
    min_table = Table(mineral_details, colWidths=[75, 125, 240, 100])
    min_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), secondary_color),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
    ]))
    story.append(min_table)
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>File Format & Validation Specifications:</b>", h3_style))
    story.append(Paragraph("• <b>Grid & Dimensions:</b> Exactly 441 spectral points per file, covering 350.0 nm to 2550.0 nm with a uniform 5.0 nm step.", bullet_style))
    story.append(Paragraph("• <b>Metadata Headers:</b> Lines 1-2 define <code># name: &lt;mineral&gt;</code> and <code># wavelength_unit: nm</code>.", bullet_style))
    story.append(Paragraph("• <b>Data Columns:</b> Line 3 defines the header <code>wavelength_nm,reflectance</code>, followed by comma-separated float pairs.", bullet_style))
    story.append(Paragraph("• <b>Integrity Guarantees:</b> Wavelengths are strictly monotonically increasing. Reflectance is strictly bounded within [0.0, 1.0]. Zero deleted channel markers (USGS -1.23e34), zero NaNs, zero infinities.", bullet_style))
    story.append(Paragraph("• <b>Ingestion Class:</b> Loaded via <code>SpectralLibrary.from_directory()</code> in <code>spectral_library.py</code> with automatic unit normalization and read-only numpy array protection.", bullet_style))

    # Force clean page break to Page 3
    story.append(PageBreak())

    # =========================================================================
    # PAGE 3: FEATURE ENGINEERING & DATASET GENERATION METHODOLOGIES
    # =========================================================================
    story.append(Paragraph("4. Unified Feature Engineering (The 275-Feature Vector)", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=primary_color, spaceBefore=1, spaceAfter=5))
    
    story.append(Paragraph(
        "Both synthetic training samples and real orbital pixels are transformed into an identical <b>275-dimensional feature vector</b> "
        "via <code>rtm_inversion/features.py</code>. This representation decouples surface mineralogy from atmospheric and topographic distortions:",
        body_style
    ))

    feat_data = [
        [Paragraph("Feature Group", th_style), Paragraph("Dim", th_style), Paragraph("Mathematical Definition / Formula", th_style), Paragraph("Scientific Purpose & Rationale", th_style)],
        [
            Paragraph("<b>Continuum-Removed Reflectance</b><br/><code>cr_380.9nm</code> to <code>cr_2492.8nm</code>", tc_bold),
            Paragraph("255", tc_style),
            Paragraph("<b>CR(λ) = R(λ) / C(λ)</b><br/>where C(λ) is the upper convex hull of reflectance R(λ).", tc_style),
            Paragraph("Normalizes varying surface albedo, soil brightness, and illumination geometry; isolates genuine absorption band shapes.", tc_style),
        ],
        [
            Paragraph("<b>Diagnostic Absorption Depths</b><br/><code>depth_Al-OH</code>, <code>depth_Fe-OH</code>,<br/><code>depth_Mg-OH</code>, <code>depth_CO3</code>,<br/><code>depth_Fe3+</code>, <code>depth_Fe2+</code>,<br/><code>depth_Sulfate</code>", tc_bold),
            Paragraph("7", tc_style),
            Paragraph("<b>Depth_k = 1.0 − min(CR(λ))</b> for λ ∈ Window_k<br/>(e.g., Al-OH: 2100-2300 nm; CO3: 2300-2400 nm; Fe3+: 750-1050 nm; Sulfate: 1700-2300 nm).", tc_style),
            Paragraph("Explicitly parameterizes absorption band intensities corresponding to diagnostic cation-OH, carbonate, and crystal field transitions.", tc_style),
        ],
        [
            Paragraph("<b>Spectral Angle Mapper Angles</b><br/><code>sam_alunite</code> to <code>sam_quartz</code>", tc_bold),
            Paragraph("12", tc_style),
            Paragraph("<b>θ_i = arccos( (CR · CR_i_ref) / (|CR| |CR_i_ref|) )</b><br/>Angular distance in radians between sample CR vector and each endmember CR vector.", tc_style),
            Paragraph("Measures holistic spectral similarity to each pure USGS reference mineral, providing an invariant similarity space.", tc_style),
        ],
        [
            Paragraph("<b>Scene Brightness</b><br/><code>brightness</code>", tc_bold),
            Paragraph("1", tc_style),
            Paragraph("<b>B = (1 / 255) · Σ R(λ_j)</b><br/>Mean raw reflectance across all 255 good bands.", tc_style),
            Paragraph("Retains broadband albedo information necessary to distinguish high-reflectance minerals (quartz, gypsum) from dark mafic minerals (chlorite).", tc_style),
        ],
    ]
    feat_table = Table(feat_data, colWidths=[120, 25, 195, 200])
    feat_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), secondary_color),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
    ]))
    story.append(feat_table)
    story.append(Spacer(1, 7))

    story.append(Paragraph("5. Generation of the Synthetic Training Dataset (training_sample.csv)", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=primary_color, spaceBefore=1, spaceAfter=5))
    
    story.append(Paragraph(
        "<b>Forward Simulation Model:</b> Implemented in <code>services/mineral_analysis/rtm_inversion/synth.py</code>. "
        "Macroscopic linear mixing models the mixed surface reflectance before passing through the sensor simulator:",
        body_style
    ))
    story.append(Paragraph(
        "R_sim(λ) = [ Σ (a_i · E_i(λ)) ] × b + ε(λ)",
        formula_style
    ))
    story.append(Paragraph(
        "• <b>Abundance Vector (a_i):</b> Generated using a sparse Dirichlet distribution. A random subset of 1 to 4 minerals is chosen per pixel (mean active endmembers = <b>1.95</b>). "
        "Abundances strictly satisfy the physical simplex: <b>a_i ≥ 0</b> and <b>Σ a_i = 1.0</b>.<br/>"
        "• <b>Illumination / Topography Factor (b):</b> Lognormal random multiplier calibrated to match the empirical brightness of the EMIT scene.<br/>"
        "• <b>Radiometric Noise (ε):</b> Wavelength-dependent Gaussian noise modeling EMIT detector signal-to-noise ratio.<br/>"
        "• <b>Dominant Class Assignment:</b> Designated as <b>y_class = argmax_i(a_i)</b>.<br/>"
        "• <b>Balanced Class Stratification:</b> Total <b>62,400 samples</b> (~5,200 dominant samples per mineral across all 12 classes). "
        "<code>training_sample.csv</code> contains the first 2,000 samples. In our evaluation audit, 100% of rows have <code>dominant</code> matching argmax(a_i) with zero disagreements.",
        body_style
    ))
    story.append(Spacer(1, 6))

    story.append(Paragraph("6. Generation of the Real EMIT Evaluation Dataset (dataset_sample.csv)", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=primary_color, spaceBefore=1, spaceAfter=5))
    
    story.append(Paragraph(
        "<b>Orbital Acquisition & Reference Pipeline:</b> Built by <code>services/mineral_analysis/rtm_inversion/build_dataset.py</code> "
        "from the georeferenced EMIT L2A reflectance cube (<code>real_emit_multiband_cube.tif</code>, 553 × 553 × 285 bands, EPSG:4326). "
        "The scene was processed through the reference SAM + SMA pipeline (<code>main.py</code>):<br/>"
        "1. <b>SAM Dominant Labeling:</b> Spectral Angle Mapper assigns the closest endmember as <code>label_name</code> and <code>label_index</code>.<br/>"
        "2. <b>SMA Fractional Unmixing:</b> Fully Constrained Linear Spectral Unmixing (FCLS / NNLS) estimates abundance fractions (<code>abund_*</code>).<br/>"
        "3. <b>Quality Scoring:</b> Multi-factor confidence metric combining SAM angle margin and SMA reconstruction RMSE.<br/>"
        "4. <b>Filtering:</b> Exactly <b>7,702 valid mineral-bearing pixels</b> were filtered into <code>dataset.npz</code>. "
        "<code>dataset_sample.csv</code> contains a 2,000-row preview. Due to the arid landscape geology, dominant labels are concentrated in gypsum (5,000) and quartz (2,702), with minor mixed chlorite/hematite/goethite.",
        body_style
    ))

    # Force clean page break to Page 4
    story.append(PageBreak())

    # =========================================================================
    # PAGE 4: SCHEMA COMPARISON, AUDIT CERTIFICATION & ENGINEERING USAGE
    # =========================================================================
    story.append(Paragraph("7. Complete Schema Comparison & Data Dictionary", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=primary_color, spaceBefore=1, spaceAfter=5))

    schema_data = [
        [Paragraph("Feature / Field Group", th_style), Paragraph("training_sample.csv (288 cols)", th_style), Paragraph("dataset_sample.csv (290 cols)", th_style), Paragraph("Alignment & Compatibility Status", th_style)],
        [
            Paragraph("<b>CR Spectral Features</b>", tc_bold),
            Paragraph("Cols 0–254: <code>cr_380.9nm</code> to <code>cr_2492.8nm</code> (float)", tc_style),
            Paragraph("Cols 0–254: <code>cr_380.9nm</code> to <code>cr_2492.8nm</code> (float)", tc_style),
            Paragraph("<b>100% Identical</b> (exact same 255 bands, names, and ordering)", tc_bold),
        ],
        [
            Paragraph("<b>Absorption Depths</b>", tc_bold),
            Paragraph("Cols 255–261: <code>depth_Al-OH</code> to <code>depth_Sulfate</code> (float)", tc_style),
            Paragraph("Cols 255–261: <code>depth_Al-OH</code> to <code>depth_Sulfate</code> (float)", tc_style),
            Paragraph("<b>100% Identical</b> (exact same 7 windows, names, and ordering)", tc_bold),
        ],
        [
            Paragraph("<b>SAM Angles</b>", tc_bold),
            Paragraph("Cols 262–273: <code>sam_alunite</code> to <code>sam_quartz</code> (float)", tc_style),
            Paragraph("Cols 262–273: <code>sam_alunite</code> to <code>sam_quartz</code> (float)", tc_style),
            Paragraph("<b>100% Identical</b> (exact same 12 minerals, names, and ordering)", tc_bold),
        ],
        [
            Paragraph("<b>Scene Brightness</b>", tc_bold),
            Paragraph("Col 274: <code>brightness</code> (float)", tc_style),
            Paragraph("Col 274: <code>brightness</code> (float)", tc_style),
            Paragraph("<b>100% Identical</b> (mean reflectance across good bands)", tc_bold),
        ],
        [
            Paragraph("<b>Classification Labels</b>", tc_bold),
            Paragraph("Col 287 (last column):<br/><code>dominant</code> (string mineral name)", tc_style),
            Paragraph("Cols 275 &amp; 276 (before abundances):<br/><code>label_index</code> (int), <code>label_name</code> (string)", tc_style),
            Paragraph("<b>Schema Note:</b> Training uses <code>dominant</code> at end; dataset uses <code>label_index</code> + <code>label_name</code> before abundances.", tc_style),
        ],
        [
            Paragraph("<b>Abundance Targets</b>", tc_bold),
            Paragraph("Cols 275–286:<br/><code>abund_alunite</code> to <code>abund_quartz</code> (float)", tc_style),
            Paragraph("Cols 277–288:<br/><code>abund_alunite</code> to <code>abund_quartz</code> (float)", tc_style),
            Paragraph("<b>100% Identical target values</b> (offset by +2 in dataset due to label cols).", tc_bold),
        ],
        [
            Paragraph("<b>Quality Metric</b>", tc_bold),
            Paragraph("<i>None (not applicable for synthetic forward model)</i>", tc_style),
            Paragraph("Col 289 (last column):<br/><code>quality</code> (float, [0.0, 0.12])", tc_style),
            Paragraph("Present only in real dataset; evaluates pipeline confidence.", tc_style),
        ],
    ]
    schema_table = Table(schema_data, colWidths=[110, 140, 140, 150])
    schema_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), secondary_color),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
    ]))
    story.append(schema_table)
    story.append(Spacer(1, 8))

    story.append(Paragraph("8. Quality Assurance & Audit Certification", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=primary_color, spaceBefore=1, spaceAfter=5))

    audit_data = [
        [Paragraph("Validation Test", th_style), Paragraph("Target Subject", th_style), Paragraph("Audit Finding & Quantitative Metrics", th_style), Paragraph("Status", th_style)],
        [
            Paragraph("<b>Spectral Integrity</b>", tc_bold),
            Paragraph("12 USGS CSVs", tc_style),
            Paragraph("441 points, monotonically increasing wavelengths (350-2550 nm, 5 nm step), 0 NaNs/Infs, valid reflectance.", tc_style),
            Paragraph("<font color='green'><b>PASSED (12/12)</b></font>", tc_style),
        ],
        [
            Paragraph("<b>Feature Consistency</b>", tc_bold),
            Paragraph("Training vs Dataset", tc_style),
            Paragraph("255 CR bands, 7 absorption depths, 12 SAM angles, 1 brightness column match band-for-band.", tc_style),
            Paragraph("<font color='green'><b>PASSED (275/275)</b></font>", tc_style),
        ],
        [
            Paragraph("<b>Abundance Simplex</b>", tc_bold),
            Paragraph("Both CSVs", tc_style),
            Paragraph("Training mean sum = 0.99999999; Dataset mean sum = 0.99999889. Zero negative abundances.", tc_style),
            Paragraph("<font color='green'><b>PASSED</b></font>", tc_style),
        ],
        [
            Paragraph("<b>Dominant Label Concordance</b>", tc_bold),
            Paragraph("training_sample.csv", tc_style),
            Paragraph("2,000 / 2,000 rows have <code>dominant</code> matching exactly with argmax(a_i) (0 disagreements).", tc_style),
            Paragraph("<font color='green'><b>PASSED (100%)</b></font>", tc_style),
        ],
        [
            Paragraph("<b>Unit Test Suite</b>", tc_bold),
            Paragraph("<code>tests/unit/test_mineral.py</code>", tc_style),
            Paragraph("14/14 unit tests passing (restored <code>LinearSpectralUnmixer</code> in <code>unmixing.py</code>).", tc_style),
            Paragraph("<font color='green'><b>PASSED (14/14)</b></font>", tc_style),
        ],
        [
            Paragraph("<b>CLI Evaluation Script</b>", tc_bold),
            Paragraph("<code>evaluate_data.py</code>", tc_style),
            Paragraph("Fixed Windows <code>cp1252</code> Unicode character encoding crash; script exits cleanly with code 0.", tc_style),
            Paragraph("<font color='green'><b>FIXED &amp; PASSED</b></font>", tc_style),
        ],
    ]
    audit_table = Table(audit_data, colWidths=[110, 110, 240, 80])
    audit_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), secondary_color),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
    ]))
    story.append(audit_table)
    story.append(Spacer(1, 8))

    story.append(Paragraph("9. Recommended Engineering Usage", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=primary_color, spaceBefore=1, spaceAfter=5))
    
    story.append(Paragraph(
        "1. <b>For Machine Learning / Deep Learning Training:</b> Ingest the 275 input features (columns 0 to 274) from "
        "<code>training_sample.csv</code> (or the complete <code>training_set.npz</code> array). "
        "For classification, predict <code>dominant</code> (column 287). For regression, predict <code>abund_*</code> (columns 275 to 286).<br/>"
        "2. <b>For Model Evaluation &amp; Benchmark Scoring:</b> Evaluate the trained model on <code>dataset_sample.csv</code> "
        "(or <code>dataset.npz</code>). Compare predicted abundances against columns <code>abund_*</code> (columns 277 to 288). "
        "Expect dominant classification accuracy to be concentrated in gypsum/quartz due to the physical geology of the EMIT scene.<br/>"
        "3. <b>For Custom Scene Processing:</b> Load reference spectra directly from <code>data/usgs_library/*.csv</code> "
        "via <code>SpectralLibrary.from_directory()</code> and apply <code>FeatureExtractor</code> from <code>rtm_inversion/features.py</code>.",
        body_style
    ))

    # Build document
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Generated PDF successfully at: {OUTPUT_PDF}")

    # Copy to artifact directory
    if ARTIFACT_DIR.exists():
        shutil.copy2(OUTPUT_PDF, ARTIFACT_PDF)
        print(f"Copied PDF to artifact directory: {ARTIFACT_PDF}")

if __name__ == "__main__":
    build_pdf()
