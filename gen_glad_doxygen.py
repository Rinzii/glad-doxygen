#!/usr/bin/env python3

# MIT License
#
# Copyright (c) 2025 Ian Pike
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
Generate Doxygen docblocks for GLAD alias macros using Khronos DocBook refpages.

USAGE
  Python:  python gen_glad_doxygen.py --in <path/to/gl.h> --xml <path/to/gl.xml> --refpages <path/to/ref/gl4> --out <path/to/out.hpp>
  PowerShell:
    py .\gen_glad_doxygen.py `
      --in       "C:\code\glad\include\glad\gl.h" `
      --xml      "C:\code\gl.xml" `
      --refpages "C:\code\ref\gl4" `
      --out      "C:\code\glad_doxygen.hpp"
"""

from __future__ import annotations
import argparse, os, re, xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple, Set

def norm_ws(s: str) -> str:
    return " ".join(s.split())

def flatten_text(elem: ET.Element) -> str:
    return "".join(elem.itertext())

def make_refpage_url(folder_tag: str, func: str) -> str:
    return f"https://registry.khronos.org/OpenGL-Refpages/{folder_tag}/html/{func}.xhtml"

class GLRegistry:
    def __init__(self, xml_path: str):
        self.root = ET.parse(xml_path).getroot()
        self.commands: Dict[str, Dict] = {}
        self.alias_of: Dict[str, str] = {}
        self.introduced_version: Dict[str, str] = {}
        self.extensions_for_cmd: Dict[str, Set[str]] = {}
        self._parse_commands()
        self._parse_features()
        self._parse_extensions()

    def _parse_commands(self) -> None:
        for cmd in self.root.findall("./commands/command"):
            proto = cmd.find("proto")
            if proto is None:
                continue
            name = proto.findtext("name")
            if not name:
                continue
            proto_text = norm_ws(flatten_text(proto))
            ret_type = norm_ws(re.sub(r"\b"+re.escape(name)+r"\b", "", proto_text))
            ret_type = norm_ws(ret_type.replace("APIENTRY", "").replace("GLAPIENTRY", ""))
            params: List[Dict[str, Optional[str]]] = []
            for p in cmd.findall("param"):
                pname = p.findtext("name")
                if not pname:
                    continue
                p_text = norm_ws(flatten_text(p))
                p_type = norm_ws(re.sub(r"\b"+re.escape(pname)+r"\b", "", p_text))
                params.append({
                    "type": p_type,
                    "name": pname,
                    "group": p.get("group"),
                    "len": p.get("len"),
                })
            alias = cmd.get("alias")
            if alias:
                self.alias_of[name] = alias
            self.commands[name] = {"ret": ret_type, "params": params, "alias": alias}

    def _parse_features(self) -> None:
        for feat in self.root.findall("./feature"):
            if feat.get("api") != "gl":
                continue
            number = feat.get("number") or ""
            if not number:
                continue
            for req in feat.findall("./require"):
                for c in req.findall("./command"):
                    nm = c.get("name")
                    if not nm:
                        continue
                    if nm not in self.introduced_version:
                        self.introduced_version[nm] = number
                    else:
                        try:
                            a = tuple(map(int, number.split(".")))
                            b = tuple(map(int, self.introduced_version[nm].split(".")))
                            if a < b:
                                self.introduced_version[nm] = number
                        except Exception:
                            pass

    def _parse_extensions(self) -> None:
        for ext in self.root.findall("./extensions/extension"):
            ext_name = ext.get("name")
            if not ext_name:
                continue
            for req in ext.findall("./require"):
                for c in req.findall("./command"):
                    nm = c.get("name")
                    if not nm:
                        continue
                    self.extensions_for_cmd.setdefault(nm, set()).add(ext_name)

    def resolve_alias(self, name: str) -> str:
        seen = set()
        cur = name
        while cur in self.alias_of and cur not in seen:
            seen.add(cur)
            cur = self.alias_of[cur]
        return cur

    def signature(self, name: str) -> Optional[Tuple[str, List[Dict[str, Optional[str]]]]]:
        info = self.commands.get(name)
        if not info:
            return None
        return info["ret"], info["params"]

    def signature_canonical(self, name: str) -> Optional[Tuple[str, List[Dict[str, Optional[str]]], str]]:
        canon = self.resolve_alias(name)
        sig = self.signature(canon)
        if not sig:
            return None
        ret, params = sig
        return ret, params, canon

    def version_or_exts(self, name: str) -> Tuple[Optional[str], List[str]]:
        v = self.introduced_version.get(name)
        if v is None:
            v = self.introduced_version.get(self.resolve_alias(name))
        exts = sorted(self.extensions_for_cmd.get(name, set()) |
                      self.extensions_for_cmd.get(self.resolve_alias(name), set()))
        return v, exts

class RefPages:
    DB_NS = {"db": "http://docbook.org/ns/docbook"}
    def __init__(self, folder: Optional[str]):
        self.folder = os.path.abspath(folder) if folder else None
        self.folder_tag = os.path.basename(self.folder) if self.folder else "gl4"
        self.cache: Dict[str, ET.Element] = {}

    def _path(self, func: str) -> Optional[str]:
        if not self.folder:
            return None
        p = os.path.join(self.folder, f"{func}.xml")
        return p if os.path.isfile(p) else None

    def _parse_lenient(self, path: str) -> Optional[ET.Element]:
        try:
            return ET.parse(path).getroot()
        except ET.ParseError:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    s = f.read()
                s = re.sub(r'<!DOCTYPE[^>]*?(\[[\s\S]*?\])?\s*>', '', s, flags=re.IGNORECASE)
                s = re.sub(r'&(?!lt;|gt;|amp;|quot;|apos;)[A-Za-z][A-Za-z0-9._-]*;', '', s)
                return ET.fromstring(s)
            except Exception:
                return None
        except Exception:
            return None

    def load(self, func: str) -> Optional[ET.Element]:
        if func in self.cache:
            return self.cache[func]
        path = self._path(func)
        if path is None:
            return None
        root = self._parse_lenient(path)
        if root is not None:
            self.cache[func] = root
        return root

    def brief(self, func: str) -> Optional[str]:
        root = self.load(func)
        if root is None:
            return None
        txt = root.findtext(".//db:refpurpose", namespaces=self.DB_NS)
        if not txt:
            txt = root.findtext(".//refpurpose")
        return norm_ws(txt) if txt else None

    def c_signature(self, func: str) -> Optional[Tuple[str, List[Tuple[str,str]]]]:
        root = self.load(func)
        if root is None:
            return None
        protos = root.findall(".//db:funcprototype", self.DB_NS) + root.findall(".//funcprototype")
        for proto in protos:
            fdef = proto.find("db:funcdef", self.DB_NS)
            if fdef is None:
                fdef = proto.find("funcdef")
            if fdef is None:
                continue
            fname = (fdef.findtext("db:function", namespaces=self.DB_NS) or fdef.findtext("function") or "").strip()
            if fname != func:
                continue
            proto_text = norm_ws(flatten_text(fdef))
            ret = norm_ws(re.sub(r"\b"+re.escape(fname)+r"\b", "", proto_text))
            params: List[Tuple[str,str]] = []
            for p in (proto.findall("db:paramdef", self.DB_NS) + proto.findall("paramdef")):
                pname = (p.findtext("db:parameter", namespaces=self.DB_NS) or p.findtext("parameter") or "").strip()
                if not pname:
                    continue
                p_text = norm_ws(flatten_text(p))
                p_type = norm_ws(re.sub(r"\b"+re.escape(pname)+r"\b", "", p_text))
                params.append((p_type, pname))
            return ret, params
        return None

    def param_descriptions(self, func: str) -> Dict[str, str]:
        root = self.load(func)
        out: Dict[str, str] = {}
        if root is None:
            return out
        sect = None
        for node in root.findall(".//db:refsect1", self.DB_NS) + root.findall(".//refsect1"):
            title = (node.findtext("db:title", namespaces=self.DB_NS) or node.findtext("title") or "").strip().lower()
            if node.get("{http://www.w3.org/XML/1998/namespace}id") in ("parameters",) or title == "parameters":
                sect = node
                break
        if sect is None:
            return out
        vlists = sect.findall(".//db:variablelist", self.DB_NS) + sect.findall(".//variablelist")
        for v in vlists:
            entries = v.findall("./db:varlistentry", self.DB_NS) + v.findall("./varlistentry")
            for e in entries:
                terms = []
                for t in e.findall("./db:term", self.DB_NS) + e.findall("./term"):
                    pname = t.findtext("db:parameter", namespaces=self.DB_NS) or t.findtext("parameter")
                    if pname:
                        terms.append(pname.strip())
                if not terms:
                    continue
                li = e.find("./db:listitem", self.DB_NS)
                if li is None:
                    li = e.find("./listitem")
                desc = norm_ws(flatten_text(li)) if li is not None else ""
                for nm in terms:
                    if desc:
                        out[nm] = desc
        return out

def make_param_line_with_desc(pname: str, ptype: str, desc: Optional[str], trailer: str = "") -> str:
    if desc:
        return f" * \\param {pname} ({ptype}) - {desc}{trailer}"
    return f" * \\param {pname} ({ptype}){trailer}"

def make_param_trailer_from_reg(reg_p: Optional[Dict[str, Optional[str]]]) -> str:
    if not reg_p:
        return ""
    extras: List[str] = []
    if reg_p.get("group"):
        extras.append(f"group: {reg_p['group']}")
    if reg_p.get("len"):
        extras.append(f"len: {reg_p['len']}")
    return ("  [" + " | ".join(extras) + "]") if extras else ""

DEFINE_RE = re.compile(r'^\s*#\s*define\s+(gl[A-Za-z0-9_]+)\s+(glad_gl[A-Za-z0-9_]+)\s*$')

def find_docblock_above(lines: List[str], idx_define: int) -> Optional[Tuple[int,int]]:
    j = idx_define - 1
    while j >= 0 and lines[j].strip() == "":
        j -= 1
    if j < 0:
        return None
    if "*/" not in lines[j]:
        if lines[j].lstrip().startswith("/**") and "*/" in lines[j]:
            return (j, j)
        return None
    end = j
    k = j
    while k >= 0:
        if "/**" in lines[k]:
            return (k, end)
        if lines[k].strip() == "":
            k -= 1
            continue
        if "*/" in lines[k] or lines[k].lstrip().startswith("*") or lines[k].lstrip().startswith("/*"):
            k -= 1
            continue
        break
    return None

def build_doc(gl_name: str, reg: GLRegistry, refs: RefPages) -> Optional[str]:
    canon = reg.resolve_alias(gl_name)
    brief = refs.brief(gl_name)
    if brief is None and canon != gl_name:
        brief = refs.brief(canon)
    sig = refs.c_signature(gl_name)
    if sig is None and canon != gl_name:
        sig = refs.c_signature(canon)
    pdesc = refs.param_descriptions(gl_name)
    if not pdesc and canon != gl_name:
        pdesc = refs.param_descriptions(canon)
    reg_sig = reg.signature_canonical(gl_name)
    reg_params_by_name: Dict[str, Dict[str, Optional[str]]] = {}
    if reg_sig:
        _ret_r, _params_r, _canon_r = reg_sig
        for rp in _params_r:
            reg_params_by_name[rp["name"]] = rp
    if sig is None and reg_sig is None:
        return None
    if sig is None and reg_sig is not None:
        ret = reg_sig[0]
        params_list = [(p["type"], p["name"]) for p in reg_sig[1]]
    else:
        ret, params_list = sig  # type: ignore
    lines: List[str] = []
    lines.append("/**")
    if brief:
        if not brief.endswith("."):
            lines.append(f" * \\brief {brief}.")
        else:
            lines.append(f" * \\brief {brief}")
    seen_p: Set[str] = set()
    for ptype, pname in params_list:
        if pname in seen_p:
            continue
        seen_p.add(pname)
        trailer = make_param_trailer_from_reg(reg_params_by_name.get(pname))
        lines.append(make_param_line_with_desc(pname, ptype, pdesc.get(pname), trailer))
    if ret and ret.strip() != "void":
        lines.append(f" * \\return ({ret})")
    lines.append(f" * \\see {make_refpage_url(refs.folder_tag, gl_name)}")
    version, exts = reg.version_or_exts(gl_name)
    note_parts: List[str] = []
    if version:
        note_parts.append(f"Introduced in OpenGL {version}")
    if exts:
        note_parts.append("Introduced by extension(s): " + ", ".join(exts))
    if note_parts:
        lines.append(" * \\note " + " | ".join(note_parts))
    lines.append(" */")
    return "\n".join(lines)

def process(in_path: str, out_path: str, reg: GLRegistry, refs: RefPages) -> None:
    with open(in_path, "r", encoding="utf-8", newline="") as f:
        src_lines = f.read().splitlines()
    skip: Set[int] = set()
    for i, line in enumerate(src_lines):
        if DEFINE_RE.match(line):
            rng = find_docblock_above(src_lines, i)
            if rng:
                a, b = rng
                for t in range(a, b + 1):
                    skip.add(t)
    out_lines: List[str] = []
    i = 0
    n = len(src_lines)
    while i < n:
        if i in skip:
            i += 1
            continue
        line = src_lines[i]
        m = DEFINE_RE.match(line)
        if m:
            gl_name = m.group(1)
            doc = build_doc(gl_name, reg, refs)
            if doc:
                out_lines.append(doc)
            out_lines.append(line)
            i += 1
            continue
        out_lines.append(line)
        i += 1
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out_lines) + "\n")

def main() -> None:
    ap = argparse.ArgumentParser(description="Insert/refresh Doxygen above GLAD alias macros using DocBook refpages; fallback to gl.xml for signature/notes when needed.")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--xml", dest="xml_path", required=True)
    ap.add_argument("--out", dest="out_path", required=True)
    ap.add_argument("--refpages", dest="refpages", required=True)
    args = ap.parse_args()
    reg = GLRegistry(args.xml_path)
    refs = RefPages(args.refpages)
    process(args.in_path, args.out_path, reg, refs)

if __name__ == "__main__":
    main()
