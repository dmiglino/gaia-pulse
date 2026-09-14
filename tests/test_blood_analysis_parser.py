"""`_extract_date` — la fecha del panel de sangre, anclada a su etiqueta.

Un informe de laboratorio trae varias fechas: nacimiento del paciente, impresión
del PDF, extracción. Tomar la primera fecha del documento (el comportamiento
anterior) puede devolver cualquiera de las otras dos.
"""

from datetime import date

from app.integrations.blood_analysis_parser import _extract_date


def test_extract_date_reads_the_extraction_label_in_spanish() -> None:
    text = (
        "Paciente: Juan Pérez\nFecha de nacimiento: 01/01/1980\nFecha de extracción: 14/03/2026\n"
    )
    assert _extract_date(text) == date(2026, 3, 14)


def test_extract_date_reads_the_collection_label_in_english() -> None:
    text = "Patient DOB: 01/01/1980\nCollection Date: 2026-03-14\nReport Date: 2026-03-18\n"
    assert _extract_date(text) == date(2026, 3, 14)


def test_extract_date_prefers_collection_over_report_date() -> None:
    """Cuándo se sacó la sangre, no cuándo se imprimió el informe."""
    text = "Report Date: 2026-03-18\nCollection Date: 2026-03-14\n"
    assert _extract_date(text) == date(2026, 3, 14)


def test_extract_date_falls_back_to_report_date_without_a_collection_label() -> None:
    text = "Fecha de informe: 18/03/2026\n"
    assert _extract_date(text) == date(2026, 3, 18)


def test_extract_date_ignores_an_unlabeled_date() -> None:
    """Sin una etiqueta reconocible, no hay fecha — no adivina la primera que aparece."""
    text = "Paciente nacido el 01/01/1980, impreso el 20/03/2026.\n"
    assert _extract_date(text) is None


def test_extract_date_ignores_a_label_whose_value_is_too_far_away() -> None:
    text = "Fecha de extracción: " + ("." * 60) + " 14/03/2026\n"
    assert _extract_date(text) is None
