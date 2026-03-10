"""
Comparador Financeiro - Sistema vs Sistema Antigo
Rode com: streamlit run app.py
Dependências: pip install streamlit pandas openpyxl
"""

import streamlit as st
import pandas as pd
import json, os, re
from datetime import datetime
from io import BytesIO

st.set_page_config(page_title="Comparador Financeiro", page_icon="🔍", layout="wide")

HISTORICO_FILE = "historico_consultas.json"

# ══════════════════════════════════════════════════════════════════════════════
#  HISTÓRICO
# ══════════════════════════════════════════════════════════════════════════════
def carregar_historico():
    if os.path.exists(HISTORICO_FILE):
        with open(HISTORICO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def salvar_historico(registros):
    with open(HISTORICO_FILE, "w", encoding="utf-8") as f:
        json.dump(registros, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
#  PARSE DOS ARQUIVOS
# ══════════════════════════════════════════════════════════════════════════════
def parse_financeiro(file) -> pd.DataFrame:
    """
    Lê o arquivo do sistema antigo.
    Estrutura fixa: blocos por médico com cabeçalho "Nome  -  123456/SP"
    seguidos de linhas de plantão: Data | Local | Tipo | Duração | Valor
    """
    df_raw = pd.read_excel(file, sheet_name=0, header=None)
    registros = []
    medico_atual = None
    crm_atual = None

    for _, row in df_raw.iterrows():
        cell0 = str(row[0]).strip() if pd.notna(row[0]) else ""

        # Detecta linha de médico: "Nome Completo  -  123456/SP"
        if re.search(r".+\s+-\s+\d+/[A-Z]{2}", cell0):
            partes = re.split(r"\s+-\s+", cell0, maxsplit=1)
            medico_atual = partes[0].strip()
            nums = re.findall(r"\d+", partes[1]) if len(partes) > 1 else []
            crm_atual = nums[0] if nums else ""
            continue

        # Detecta linha de plantão: célula 0 é uma data válida
        try:
            data = pd.to_datetime(cell0, dayfirst=True, errors="coerce")
            valor = row[4]
            if pd.notna(data) and pd.notna(valor) and medico_atual:
                registros.append({
                    "medico": medico_atual,
                    "crm": crm_atual,
                    "data": data.date(),
                    "local": str(row[1]).strip() if pd.notna(row[1]) else "",
                    "tipo": str(row[2]).strip() if pd.notna(row[2]) else "",
                    "duracao": str(row[3]).strip() if pd.notna(row[3]) else "",
                    "valor_financeiro": float(valor),
                })
        except Exception:
            pass

    return pd.DataFrame(registros)


def detectar_coluna(df, candidatos):
    """Busca coluna por nome exato ou parcial, case-insensitive."""
    cols_lower = {c.lower().strip(): c for c in df.columns}
    for c in candidatos:
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    for c in candidatos:
        for col_key, col_real in cols_lower.items():
            if c.lower() in col_key:
                return col_real
    return None


def parse_relatorio(file) -> pd.DataFrame:
    """
    Lê o arquivo do novo sistema.
    Suporta CSV com ; ou , e xlsx tabular.
    Detecta colunas automaticamente pelo conteúdo.
    """
    content = file.read(300)
    file.seek(0)

    if b";" in content:
        df = pd.read_csv(file, sep=";", encoding="utf-8-sig")
    elif b"," in content[:200]:
        try:
            df = pd.read_csv(file, sep=",", encoding="utf-8-sig")
        except Exception:
            file.seek(0)
            df = pd.read_excel(file, engine="openpyxl")
    else:
        df = pd.read_excel(file, engine="openpyxl")

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # CRM
    col_crm = detectar_coluna(df, [
        "registro_profissinal", "registro_profissional", "crm", "registro", "matricula"
    ])
    df["crm"] = (
        df[col_crm].astype(str).str.strip().apply(
            lambda x: re.findall(r"\d+", x)[0] if re.findall(r"\d+", x) else x
        ) if col_crm else "N/A"
    )

    # Nome
    col_nome = detectar_coluna(df, ["nome_completo", "nome", "medico", "profissional", "name"])
    df["medico"] = df[col_nome].astype(str).str.strip() if col_nome else "N/A"

    # Data
    col_data = detectar_coluna(df, ["data_do_plantao", "data_plantao", "data", "date"])
    df["data"] = pd.to_datetime(df[col_data], errors="coerce").dt.date if col_data else None

    # Valor líquido (o que o médico recebe — equivalente ao campo Valor do financeiro)
    col_vliq = detectar_coluna(df, ["valor_liquido", "valor_líquido", "vliquido", "valor_liq", "valor"])
    if col_vliq:
        df["valor_relatorio"] = (
            df[col_vliq].astype(str)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .pipe(pd.to_numeric, errors="coerce")
        )
    else:
        df["valor_relatorio"] = None

    return df


# ══════════════════════════════════════════════════════════════════════════════
#  COMPARAÇÃO
# ══════════════════════════════════════════════════════════════════════════════
def comparar(df_fin: pd.DataFrame, df_rel: pd.DataFrame) -> pd.DataFrame:
    inconsistencias = []
    df_fin = df_fin.copy()
    df_rel = df_rel.copy()

    # Chave de cruzamento: CRM + Data
    df_fin["chave"] = df_fin["crm"].astype(str) + "_" + df_fin["data"].astype(str)
    df_rel["chave"] = df_rel["crm"].astype(str) + "_" + df_rel["data"].astype(str)

    # Filtra o relatório apenas pelos CRMs presentes no financeiro
    crms_fin = set(df_fin["crm"].astype(str))
    df_rel_f = df_rel[df_rel["crm"].astype(str).isin(crms_fin)].copy()

    fin_ch = set(df_fin["chave"])
    rel_ch = set(df_rel_f["chave"])

    # 1. No financeiro mas falta no relatório
    for chave in fin_ch - rel_ch:
        row = df_fin[df_fin["chave"] == chave].iloc[0]
        inconsistencias.append({
            "tipo": "❌ Ausente no Novo Sistema",
            "medico": row["medico"], "crm": row["crm"], "data": str(row["data"]),
            "valor_financeiro": row["valor_financeiro"], "valor_relatorio": None,
            "diferenca": None,
            "detalhe": "Plantão no sistema antigo, ausente no novo sistema",
        })

    # 2. No relatório mas falta no financeiro (apenas médicos do financeiro)
    for chave in rel_ch - fin_ch:
        row = df_rel_f[df_rel_f["chave"] == chave].iloc[0]
        inconsistencias.append({
            "tipo": "⚠️ Ausente no Sistema Antigo",
            "medico": row.get("medico", "N/A"), "crm": row.get("crm", "N/A"),
            "data": str(row.get("data", "")),
            "valor_financeiro": None, "valor_relatorio": row.get("valor_relatorio"),
            "diferenca": None,
            "detalhe": "Plantão no novo sistema, ausente no sistema antigo",
        })

    # 3. Nos dois — compara valor
    for chave in fin_ch & rel_ch:
        r_fin = df_fin[df_fin["chave"] == chave].iloc[0]
        r_rel = df_rel_f[df_rel_f["chave"] == chave].iloc[0]
        val_fin = r_fin["valor_financeiro"]
        val_rel = r_rel.get("valor_relatorio")
        if pd.notna(val_fin) and pd.notna(val_rel):
            diff = round(float(val_fin) - float(val_rel), 2)
            if abs(diff) > 0.01:
                inconsistencias.append({
                    "tipo": "💰 Divergência de Valor",
                    "medico": r_fin["medico"], "crm": r_fin["crm"], "data": str(r_fin["data"]),
                    "valor_financeiro": val_fin, "valor_relatorio": val_rel,
                    "diferenca": diff,
                    "detalhe": f"Diferença de R$ {diff:,.2f}",
                })

    return pd.DataFrame(inconsistencias) if inconsistencias else pd.DataFrame()


def gerar_excel(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Inconsistências")
    return output.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
#  INTERFACE
# ══════════════════════════════════════════════════════════════════════════════
tabs = st.tabs(["🔍 Comparação", "📋 Histórico"])

# ─── ABA 1: COMPARAÇÃO ────────────────────────────────────────────────────────
with tabs[0]:
    st.title("🔍 Comparador Financeiro")
    st.markdown("Faça upload dos dois arquivos nos campos corretos e clique em **Comparar**.")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📂 Sistema Antigo")
        st.caption("Arquivo com plantões agrupados por médico (ex: exportação do sistema financeiro)")
        file_fin = st.file_uploader(
            "Selecione o arquivo do Sistema Antigo",
            type=["xlsx", "xls"],
            key="fin",
            help="Qualquer nome de arquivo — o que importa é o conteúdo"
        )
    with col2:
        st.subheader("📂 Novo Sistema")
        st.caption("Relatório tabular com uma linha por plantão (ex: relatório de lucro)")
        file_rel = st.file_uploader(
            "Selecione o arquivo do Novo Sistema",
            type=["xlsx", "xls", "csv"],
            key="rel",
            help="Qualquer nome de arquivo — o que importa é o conteúdo"
        )

    if file_fin and file_rel:
        if st.button("🔍 Comparar agora", type="primary", use_container_width=True):
            with st.spinner("Processando e comparando..."):
                try:
                    df_fin = parse_financeiro(file_fin)
                    df_rel = parse_relatorio(file_rel)
                except Exception as e:
                    st.error(f"Erro ao processar arquivos: {e}")
                    st.stop()

            # Guarda no session_state para não reprocessar ao interagir com filtros
            st.session_state["df_fin"] = df_fin
            st.session_state["df_rel"] = df_rel
            st.session_state["df_inc"] = comparar(df_fin, df_rel)
            st.session_state["nome_fin"] = file_fin.name
            st.session_state["nome_rel"] = file_rel.name

    # Exibe resultados se já processado
    if "df_inc" in st.session_state:
        df_fin = st.session_state["df_fin"]
        df_rel = st.session_state["df_rel"]
        df_inc = st.session_state["df_inc"]
        nome_fin = st.session_state["nome_fin"]
        nome_rel = st.session_state["nome_rel"]

        crms_fin = set(df_fin["crm"].astype(str))
        df_rel_filtrado = df_rel[df_rel["crm"].astype(str).isin(crms_fin)]

        st.divider()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Médicos comparados", df_fin["medico"].nunique())
        c2.metric("Plantões — Sistema Antigo", len(df_fin))
        c3.metric("Plantões — Novo Sistema (grupo)", len(df_rel_filtrado))
        c4.metric("Plantões — Novo Sistema (total)", len(df_rel))

        with st.expander("👥 Ver médicos comparados"):
            med_fin = df_fin[["medico", "crm"]].drop_duplicates().sort_values("medico")
            med_rel = (
                df_rel_filtrado[["medico", "crm"]].drop_duplicates()
                .rename(columns={"medico": "nome_no_novo_sistema"})
            )
            merged = med_fin.merge(med_rel, on="crm", how="left")
            merged["Encontrado"] = merged["nome_no_novo_sistema"].notna().map(
                {True: "✅ Sim", False: "❌ Não"}
            )
            st.dataframe(
                merged[["medico", "crm", "nome_no_novo_sistema", "Encontrado"]],
                use_container_width=True, hide_index=True
            )

        st.divider()
        if df_inc.empty:
            st.success("✅ Nenhuma inconsistência encontrada! Os arquivos estão alinhados.")
        else:
            tipos = df_inc["tipo"].value_counts()
            st.error(f"⚠️ **{len(df_inc)} inconsistências encontradas**")

            c1, c2, c3 = st.columns(3)
            c1.metric("❌ Ausentes no Novo Sistema",   tipos.get("❌ Ausente no Novo Sistema", 0))
            c2.metric("⚠️ Ausentes no Sistema Antigo", tipos.get("⚠️ Ausente no Sistema Antigo", 0))
            c3.metric("💰 Divergências de Valor",      tipos.get("💰 Divergência de Valor", 0))

            filtro = st.multiselect(
                "Filtrar por tipo",
                options=df_inc["tipo"].unique().tolist(),
                default=df_inc["tipo"].unique().tolist(),
            )
            df_show = df_inc[df_inc["tipo"].isin(filtro)]
            st.dataframe(
                df_show[["tipo", "medico", "crm", "data",
                          "valor_financeiro", "valor_relatorio", "diferenca", "detalhe"]],
                use_container_width=True, hide_index=True,
            )

            excel_bytes = gerar_excel(df_show)
            st.download_button(
                "⬇️ Baixar Inconsistências (.xlsx)",
                data=excel_bytes,
                file_name=f"inconsistencias_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        st.divider()
        if st.button("💾 Salvar esta consulta no Histórico"):
            historico = carregar_historico()
            resumo_inc = df_inc.groupby("tipo").size().to_dict() if not df_inc.empty else {}
            historico.append({
                "data_consulta": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "arquivo_financeiro": nome_fin,
                "arquivo_relatorio": nome_rel,
                "total_financeiro": len(df_fin),
                "total_relatorio": len(df_rel),
                "total_inconsistencias": len(df_inc),
                "resumo_tipos": resumo_inc,
                "inconsistencias": (
                    df_inc.fillna("").astype(str).to_dict(orient="records")
                    if not df_inc.empty else []
                ),
            })
            salvar_historico(historico)
            st.success("✅ Consulta salva no histórico!")


# ─── ABA 2: HISTÓRICO ─────────────────────────────────────────────────────────
with tabs[1]:
    st.title("📋 Histórico de Consultas")
    historico = carregar_historico()

    if not historico:
        st.info("Nenhuma consulta salva ainda. Faça uma comparação e clique em **Salvar**.")
    else:
        st.markdown(f"**{len(historico)} consulta(s) salva(s)**")

        for i, reg in enumerate(reversed(historico), 1):
            with st.expander(
                f"🕒 {reg['data_consulta']} — {reg['total_inconsistencias']} inconsistência(s)"
            ):
                c1, c2, c3 = st.columns(3)
                c1.markdown(f"**Sistema Antigo:** `{reg['arquivo_financeiro']}`")
                c2.markdown(f"**Novo Sistema:** `{reg['arquivo_relatorio']}`")
                c3.metric("Inconsistências", reg["total_inconsistencias"])

                if reg.get("resumo_tipos"):
                    st.write("**Por tipo:**", reg["resumo_tipos"])

                if reg.get("inconsistencias"):
                    df_hist = pd.DataFrame(reg["inconsistencias"])
                    st.dataframe(df_hist, use_container_width=True, hide_index=True)
                    excel_bytes = gerar_excel(df_hist)
                    st.download_button(
                        "⬇️ Baixar esta consulta (.xlsx)",
                        data=excel_bytes,
                        file_name=f"historico_{i}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dl_hist_{i}",
                    )

        st.divider()
        if st.button("🗑️ Limpar todo o histórico"):
            salvar_historico([])
            st.success("Histórico limpo!")
            st.rerun()