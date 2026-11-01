"""Image du QR Code (SVG, bibliothèque Segno), générée par le serveur à l'émission du jeton.

Correction d'erreur de niveau Q (environ 25 % du symbole restaurable : support de table taché,
plié ou mal éclairé), marge normalisée de 4 modules, contraste maximal (noir sur blanc). L'image
n'est jamais conservée : elle n'existe que dans la réponse d'émission ou de régénération."""

import io

import segno

QR_ERROR_LEVEL = "q"
QR_BORDER = 4


def qr_svg(url: str) -> str:
    code = segno.make_qr(url, error=QR_ERROR_LEVEL, boost_error=False)
    out = io.BytesIO()
    code.save(
        out,
        kind="svg",
        scale=1,
        omitsize=True,
        border=QR_BORDER,
        dark="#000000",
        light="#ffffff",
        xmldecl=False,
        svgns=True,
        title=None,
        desc=None,
        unit=None,
    )
    return out.getvalue().decode("utf-8")
