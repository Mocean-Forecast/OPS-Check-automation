"""HTML preview of the Teams card for dry runs: open it in a browser, rows with ▸ fold open.

Uses the official Adaptive Cards renderer from a CDN (needs internet in the browser). Teams uses
its own renderer, so spacing differs slightly; content and folding are the same.
"""

from __future__ import annotations

import json
from typing import Any

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;background:#f5f5f5;font:14px/1.45 "Segoe UI",system-ui,sans-serif;color:#242424}}
.w{{max-width:860px;margin:0 auto;padding:20px 16px}}.n{{color:#616161;font-size:12.5px}}
.c{{background:#fff;border:1px solid #e1dfdd;border-radius:6px;padding:14px 16px}}.c p{{margin:0}}
.ac-selectable{{cursor:pointer;border-radius:4px}}.ac-selectable:hover{{background:rgba(127,127,127,.12)}}</style>
<script src="https://cdn.jsdelivr.net/npm/markdown-it@14.1.0/dist/markdown-it.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/adaptivecards@3.0.5/dist/adaptivecards.js"></script>
</head><body><div class="w"><p class="n">{note}</p><div class="c" id="h"></div></div><script>
const CARD={card};
const f=(d,s)=>({{default:d,subtle:s}});
const col={{default:f("#242424","#616161"),dark:f("#242424","#616161"),light:f("#242424","#616161"),
 accent:f("#5B5FC7","#5B5FC7"),good:f("#237B4B","#237B4B"),warning:f("#835C00","#835C00"),attention:f("#C4314B","#C4314B")}};
const hc=new AdaptiveCards.HostConfig({{fontFamily:"Segoe UI, system-ui, sans-serif",
 spacing:{{small:4,default:8,medium:14,large:18,extraLarge:24,padding:10}},separator:{{lineThickness:1,lineColor:"#E1DFDD"}},
 fontSizes:{{small:12,default:14,medium:15,large:18,extraLarge:22}},fontWeights:{{lighter:300,default:400,bolder:600}},
 containerStyles:{{default:{{backgroundColor:"#FFFFFF",foregroundColors:col}},emphasis:{{backgroundColor:"#F5F5F5",foregroundColors:col}}}}}});
const md=window.markdownit({{breaks:true}});
AdaptiveCards.AdaptiveCard.onProcessMarkdown=(t,r)=>{{r.outputHtml=md.render(t);r.didProcess=true;}};
const c=new AdaptiveCards.AdaptiveCard();c.hostConfig=hc;c.parse(CARD);document.getElementById("h").appendChild(c.render());
</script></body></html>"""


def card_preview_html(card: dict[str, Any], note: str = "") -> str:
    return _PAGE.format(title="OPS Check preview", note=note,
                        card=json.dumps(card, ensure_ascii=False).replace("</", "<\\/"))
