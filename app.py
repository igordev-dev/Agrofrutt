from flask import Flask, render_template, request, redirect, url_for, Response
import csv, io, os
from datetime import date

app = Flask(__name__)

# Lê a URL do banco definida como variável de ambiente no Render.
# Em desenvolvimento local, cai para SQLite automaticamente.
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras

    def get_db():
        # Conexão com PostgreSQL (Supabase)
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn

    # Placeholder do PostgreSQL é %s (diferente do ? do SQLite)
    PH = "%s"
else:
    import sqlite3

    def get_db():
        # Conexão local com SQLite para desenvolvimento
        conn = sqlite3.connect("agrofrut.db")
        conn.row_factory = sqlite3.Row
        return conn

    PH = "?"


def init_db():
    """Cria as tabelas caso ainda não existam. Compatível com PostgreSQL e SQLite."""
    db = get_db()
    cur = db.cursor()

    if DATABASE_URL:
        # PostgreSQL usa SERIAL em vez de AUTOINCREMENT e CURRENT_DATE para data
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fornecedores (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                telefone TEXT,
                produto TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                telefone TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS estoque (
                id SERIAL PRIMARY KEY,
                produto TEXT NOT NULL,
                tipo_caixa TEXT NOT NULL,
                quantidade INTEGER NOT NULL DEFAULT 0,
                fornecedor_id INTEGER REFERENCES fornecedores(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vendas (
                id SERIAL PRIMARY KEY,
                cliente_id INTEGER REFERENCES clientes(id),
                estoque_id INTEGER REFERENCES estoque(id),
                quantidade INTEGER NOT NULL,
                valor_unitario NUMERIC(10,2) NOT NULL DEFAULT 0,
                data DATE DEFAULT CURRENT_DATE
            )
        """)
    else:
        # SQLite: usa executescript para múltiplos statements
        db.executescript("""
            CREATE TABLE IF NOT EXISTS fornecedores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL, telefone TEXT, produto TEXT
            );
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL, telefone TEXT
            );
            CREATE TABLE IF NOT EXISTS estoque (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produto TEXT NOT NULL, tipo_caixa TEXT NOT NULL,
                quantidade INTEGER NOT NULL DEFAULT 0,
                fornecedor_id INTEGER REFERENCES fornecedores(id)
            );
            CREATE TABLE IF NOT EXISTS vendas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER REFERENCES clientes(id),
                estoque_id INTEGER REFERENCES estoque(id),
                quantidade INTEGER NOT NULL,
                valor_unitario REAL NOT NULL DEFAULT 0,
                data TEXT DEFAULT (date('now'))
            );
        """)
        # Migração: adiciona fornecedor_id se o banco local já existia sem ela
        try:
            db.execute("ALTER TABLE estoque ADD COLUMN fornecedor_id INTEGER")
        except Exception:
            pass

    db.commit()
    cur.close() if DATABASE_URL else None
    db.close()


init_db()


# ── HELPERS ────────────────────────────────────────────────────────────────────

def query(sql, params=()):
    """Executa SELECT e retorna lista de linhas como dicionário."""
    db = get_db()
    cur = db.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    db.close()
    # Converte RealDictRow para dict comum para uniformidade
    return [dict(r) for r in rows]


def execute(sql, params=()):
    """Executa INSERT/UPDATE/DELETE com commit automático."""
    db = get_db()
    cur = db.cursor()
    cur.execute(sql, params)
    db.commit()
    cur.close()
    db.close()


def _filtro_periodo(data_ini, data_fim):
    """Monta cláusula WHERE e parâmetros para filtro de período."""
    filtro, params = "", []
    if data_ini:
        filtro += f" AND v.data >= {PH}"
        params.append(data_ini)
    if data_fim:
        filtro += f" AND v.data <= {PH}"
        params.append(data_fim)
    return filtro, params


# ── PÁGINA INICIAL ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    # Estoque com nome do fornecedor vinculado
    estoque = query("""
        SELECT e.*, f.nome as fornecedor_nome
        FROM estoque e LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        ORDER BY e.produto
    """)
    clientes     = query("SELECT * FROM clientes ORDER BY nome")
    fornecedores = query("SELECT * FROM fornecedores ORDER BY nome")

    # Faturamento total acumulado
    row = query("SELECT COALESCE(SUM(quantidade * valor_unitario), 0) as total FROM vendas")
    faturamento = float(row[0]["total"])

    # Produto com maior volume de saída
    mv = query("""
        SELECT e.produto, e.tipo_caixa, SUM(v.quantidade) as total
        FROM vendas v JOIN estoque e ON v.estoque_id = e.id
        GROUP BY e.produto, e.tipo_caixa ORDER BY total DESC LIMIT 1
    """)
    mais_vendido = mv[0] if mv else None

    # Itens com quantidade igual ou abaixo de 10 caixas
    estoque_baixo = query("SELECT * FROM estoque WHERE quantidade <= 10 ORDER BY quantidade")

    return render_template("index.html",
        estoque=estoque, clientes=clientes, fornecedores=fornecedores,
        faturamento=faturamento, mais_vendido=mais_vendido, estoque_baixo=estoque_baixo)


# ── ESTOQUE ────────────────────────────────────────────────────────────────────

@app.route("/estoque/add", methods=["POST"])
def add_estoque():
    produto = request.form["produto"].strip()
    tipo    = request.form["tipo_caixa"]
    qtd     = int(request.form["quantidade"])
    forn_id = request.form.get("fornecedor_id") or None

    # Se produto+tipo já existe, soma a quantidade em vez de duplicar
    existente = query(
        f"SELECT id FROM estoque WHERE produto={PH} AND tipo_caixa={PH}", (produto, tipo)
    )
    if existente:
        execute(
            f"UPDATE estoque SET quantidade = quantidade + {PH}, fornecedor_id={PH} WHERE id={PH}",
            (qtd, forn_id, existente[0]["id"])
        )
    else:
        execute(
            f"INSERT INTO estoque (produto, tipo_caixa, quantidade, fornecedor_id) VALUES ({PH},{PH},{PH},{PH})",
            (produto, tipo, qtd, forn_id)
        )
    return redirect(url_for("index"))


@app.route("/estoque/edit/<int:id>", methods=["POST"])
def edit_estoque(id):
    execute(
        f"UPDATE estoque SET produto={PH}, tipo_caixa={PH}, quantidade={PH}, fornecedor_id={PH} WHERE id={PH}",
        (request.form["produto"].strip(), request.form["tipo_caixa"],
         int(request.form["quantidade"]), request.form.get("fornecedor_id") or None, id)
    )
    return redirect(url_for("index"))


@app.route("/estoque/delete/<int:id>")
def del_estoque(id):
    execute(f"DELETE FROM estoque WHERE id={PH}", (id,))
    return redirect(url_for("index"))


# ── VENDAS ─────────────────────────────────────────────────────────────────────

@app.route("/venda/add", methods=["POST"])
def add_venda():
    cliente_id = request.form["cliente_id"]
    estoque_id = request.form["estoque_id"]
    qtd        = int(request.form["quantidade"])
    valor      = float(request.form["valor_unitario"])

    item = query(f"SELECT quantidade FROM estoque WHERE id={PH}", (estoque_id,))

    # Bloqueia a venda se não houver estoque suficiente
    if not item or item[0]["quantidade"] < qtd:
        return "Estoque insuficiente", 400

    # Baixa no estoque e registra a venda
    execute(f"UPDATE estoque SET quantidade = quantidade - {PH} WHERE id={PH}", (qtd, estoque_id))
    execute(
        f"INSERT INTO vendas (cliente_id, estoque_id, quantidade, valor_unitario) VALUES ({PH},{PH},{PH},{PH})",
        (cliente_id, estoque_id, qtd, valor)
    )
    return redirect(url_for("index"))


# ── CLIENTES ───────────────────────────────────────────────────────────────────

@app.route("/clientes")
def clientes():
    lista = query("SELECT * FROM clientes ORDER BY nome")

    # Últimas 50 vendas com dados do cliente e do produto
    historico = query("""
        SELECT c.nome as cliente, e.produto, e.tipo_caixa,
               v.quantidade, v.valor_unitario, v.data
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        ORDER BY v.data DESC LIMIT 50
    """)
    return render_template("clientes.html", clientes=lista, historico=historico)


@app.route("/clientes/add", methods=["POST"])
def add_cliente():
    execute(
        f"INSERT INTO clientes (nome, telefone) VALUES ({PH},{PH})",
        (request.form["nome"].strip(), request.form["telefone"].strip())
    )
    return redirect(url_for("clientes"))


@app.route("/clientes/edit/<int:id>", methods=["POST"])
def edit_cliente(id):
    execute(
        f"UPDATE clientes SET nome={PH}, telefone={PH} WHERE id={PH}",
        (request.form["nome"].strip(), request.form["telefone"].strip(), id)
    )
    return redirect(url_for("clientes"))


@app.route("/clientes/delete/<int:id>")
def del_cliente(id):
    execute(f"DELETE FROM clientes WHERE id={PH}", (id,))
    return redirect(url_for("clientes"))


# ── FORNECEDORES ───────────────────────────────────────────────────────────────

@app.route("/fornecedores")
def fornecedores():
    lista = query("SELECT * FROM fornecedores ORDER BY nome")
    return render_template("fornecedores.html", fornecedores=lista)


@app.route("/fornecedores/add", methods=["POST"])
def add_fornecedor():
    execute(
        f"INSERT INTO fornecedores (nome, telefone, produto) VALUES ({PH},{PH},{PH})",
        (request.form["nome"].strip(), request.form["telefone"].strip(), request.form["produto"].strip())
    )
    return redirect(url_for("fornecedores"))


@app.route("/fornecedores/edit/<int:id>", methods=["POST"])
def edit_fornecedor(id):
    execute(
        f"UPDATE fornecedores SET nome={PH}, telefone={PH}, produto={PH} WHERE id={PH}",
        (request.form["nome"].strip(), request.form["telefone"].strip(), request.form["produto"].strip(), id)
    )
    return redirect(url_for("fornecedores"))


@app.route("/fornecedores/delete/<int:id>")
def del_fornecedor(id):
    execute(f"DELETE FROM fornecedores WHERE id={PH}", (id,))
    return redirect(url_for("fornecedores"))


# ── RELATÓRIO ──────────────────────────────────────────────────────────────────

@app.route("/relatorio")
def relatorio():
    data_ini = request.args.get("data_ini", "")
    data_fim = request.args.get("data_fim", str(date.today()))
    filtro, params = _filtro_periodo(data_ini, data_fim)

    # Todas as vendas do período com dados completos
    vendas = query(f"""
        SELECT v.data, c.nome as cliente, e.produto, e.tipo_caixa,
               v.quantidade, v.valor_unitario,
               (v.quantidade * v.valor_unitario) as total,
               f.nome as fornecedor
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        WHERE 1=1 {filtro}
        ORDER BY v.data DESC
    """, params)

    faturamento = sum(float(r["total"]) for r in vendas)

    # Ranking de produtos por quantidade vendida no período
    por_produto = query(f"""
        SELECT e.produto, e.tipo_caixa,
               SUM(v.quantidade) as qtd_total,
               SUM(v.quantidade * v.valor_unitario) as receita
        FROM vendas v JOIN estoque e ON v.estoque_id = e.id
        WHERE 1=1 {filtro}
        GROUP BY e.produto, e.tipo_caixa ORDER BY qtd_total DESC
    """, params)

    # Posição atual do estoque (sem filtro de data)
    estoque = query("""
        SELECT e.produto, e.tipo_caixa, e.quantidade, f.nome as fornecedor
        FROM estoque e LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        ORDER BY e.produto
    """)

    return render_template("relatorio.html",
        vendas=vendas, faturamento=faturamento,
        por_produto=por_produto, estoque=estoque,
        data_ini=data_ini, data_fim=data_fim)


@app.route("/relatorio/csv")
def relatorio_csv():
    data_ini = request.args.get("data_ini", "")
    data_fim = request.args.get("data_fim", str(date.today()))
    filtro, params = _filtro_periodo(data_ini, data_fim)

    vendas = query(f"""
        SELECT v.data, c.nome as cliente, e.produto, e.tipo_caixa,
               v.quantidade, v.valor_unitario,
               (v.quantidade * v.valor_unitario) as total,
               f.nome as fornecedor
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        WHERE 1=1 {filtro}
        ORDER BY v.data DESC
    """, params)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Data", "Cliente", "Produto", "Tipo Caixa", "Qtd", "Valor Unit. (R$)", "Total (R$)", "Fornecedor"])
    for r in vendas:
        writer.writerow([
            r["data"], r["cliente"], r["produto"], r["tipo_caixa"],
            r["quantidade"], f'{float(r["valor_unitario"]):.2f}',
            f'{float(r["total"]):.2f}', r["fornecedor"] or ""
        ])

    output.seek(0)
    # BOM UTF-8 garante que o Excel abra com acentos corretamente
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=relatorio_agrofrut.csv"}
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
