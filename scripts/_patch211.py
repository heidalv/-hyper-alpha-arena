import re, pathlib
p = pathlib.Path("scripts/_probe211_mm_flatten_reason.py")
s = p.read_text(encoding="utf-8")
s = s.replace("round(sum(net_bp::numeric*notional/10000.0),1)", "round((sum(net_bp::numeric*notional::numeric/10000.0))::numeric,1)")
s = s.replace("round(avg(net_bp::numeric),3)", "round(avg(net_bp::numeric),3)")
s = s.replace("round(avg(age_s),0)", "round(avg(age_s)::numeric,0)")
s = s.replace("round(percentile_cont(0.5) within group (order by age_s),0)", "round((percentile_cont(0.5) within group (order by age_s))::numeric,0)")
p.write_text(s, encoding="utf-8")
print("patched")
