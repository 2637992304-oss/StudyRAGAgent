"""Self-authored relations; no private course material in these fixtures."""

from types import SimpleNamespace

from app.db.models import Chunk, Document
from app.retrieval import Candidate
from app.table_evidence import Catalog, cell_key, render_verified, table_plan


def library():
    specs = {
        "department": (
            "dept_name varchar(20),\nbudget numeric(10),\nprimary key (dept_name)",
            "dept_name budget\nComputing 50000",
        ),
        "instructor": (
            "ID varchar(5),\nname varchar(20),\ndept_name varchar(20),\n"
            "salary numeric(8),\nprimary key (ID),\n"
            "foreign key (dept_name) references department",
            "ID name dept_name salary\n70001 Ada Computing 60000\n"
            "70002 Ben Computing 61000",
        ),
        "classroom": (
            "building varchar(15),\nroom_number varchar(7),\ncapacity numeric(4),\n"
            "primary key (building,room_number)",
            "building room_number capacity\nNorth 100 30\nNorth 120 90",
        ),
        "section": (
            "course_id varchar(8),\nsec_id varchar(8),\nsemester varchar(6),\n"
            "year numeric(4),\nbuilding varchar(15),\nroom_number varchar(7),\n"
            "primary key (course_id,sec_id,semester,year),\n"
            "foreign key (building,room_number) references classroom",
            "course_id sec_id semester year building room_number\n"
            "LAB-42 1 Spring 2025 North 100\nLAB-42 2 Spring 2025 North 120\n"
            "LAB-41 1 Spring 2025 North 120",
        ),
        "teaches": (
            "ID varchar(5),\ncourse_id varchar(8),\nsec_id varchar(8),\n"
            "semester varchar(6),\nyear numeric(4),\n"
            "primary key (ID,course_id,sec_id,semester,year),\n"
            "foreign key (ID) references instructor,\n"
            "foreign key (course_id,sec_id,semester,year) references section",
            "ID course_id sec_id semester year\n70001 LAB-42 1 Spring 2025\n"
            "70002 LAB-42 2 Spring 2025\n70002 LAB-41 1 Spring 2025",
        ),
        "student": (
            "ID varchar(5),\nname varchar(20),\ntot_cred numeric(3),\nprimary key (ID)",
            "ID name tot_cred\n80001 Alex 0\n80002 Blair 12",
        ),
        "advisor": (
            "s_ID varchar(5),\ni_ID varchar(5),\nprimary key (s_ID),\n"
            "foreign key (s_ID) references student(ID),\n"
            "foreign key (i_ID) references instructor(ID)",
            "s_ID i_ID\n80001 70001",
        ),
        "takes": (
            "ID varchar(5),\ncourse_id varchar(8),\ngrade varchar(2),\n"
            "primary key (ID,course_id),\nforeign key (ID) references student",
            "ID course_id grade\n80001 LAB-42 null",
        ),
    }
    items = []
    for i, (name, (ddl, rows)) in enumerate(specs.items()):
        for kind, text in (
            ("ddl", f"create table {name}\n({ddl});"),
            ("data", f"Figure B.{i + 1} The {name} relation.\n{rows}"),
        ):
            items.append(
                Candidate(
                    Chunk(
                        id=f"{name}-{kind}",
                        text=text,
                        chunk_index=len(items),
                        page_start=i + 1,
                    ),
                    Document(id="test-book"),
                )
            )
    return items


ALIASES = {
    "教师": ["instructor", "teaches"],
    "教室": ["classroom"],
    "学生": ["student"],
    "导师": ["advisor", "instructor"],
    "成绩": ["takes"],
    "学分": ["tot_cred"],
}


def test_composite_keys_prevent_neighbor_room_and_section_instructor_mixup():
    items = library()
    plan, required = table_plan(
        items,
        {"section-data", "teaches-data"},
        "LAB-42 Spring 2025 section 1的教师姓名、薪水、教室和容量？",
        ALIASES,
    )
    assert plan["status"] == "verified_rows"
    rows = {row["table"]: row["values"] for row in plan["rows"]}
    assert rows["instructor"]["id"] == "70001"
    assert rows["instructor"]["name"] == "Ada"
    assert rows["classroom"] == {
        "building": "North",
        "roomnumber": "100",
        "capacity": "30",
    }
    join = next(
        j for j in plan["joins"] if j["from"] == "teaches" and j["to"] == "section"
    )
    assert join["columns"] == ["courseid", "secid", "semester", "year"]
    evidence = [x for x in items if x.chunk.id in required]
    answer = render_verified(plan, evidence)
    assert "roomnumber=100" in answer and "roomnumber=120" not in answer
    assert "name=Ben" not in answer
    assert render_verified(plan, []) is None


def test_advisor_role_keys_no_same_name_column_inference_and_budget_second_hop():
    items = library()
    plan, _ = table_plan(
        items,
        {"advisor-data", "student-data"},
        "学生80001的导师ID和姓名？用advisor外键确认",
        ALIASES,
    )
    assert {row["values"].get("name") for row in plan["rows"]} >= {"Alex", "Ada"}
    assert any(
        j["columns"] == ["iid"] and j["references"] == ["id"] for j in plan["joins"]
    )
    budget, _ = table_plan(
        items, {"instructor-data"}, "Ada的工资和预算是多少？", ALIASES
    )
    assert (
        next(r for r in budget["rows"] if r["table"] == "department")["values"][
            "budget"
        ]
        == "50000"
    )
    # No initial admitted source -> no exact-key bypass of the relevance gate.
    assert (
        table_plan(items, set(), "Ada的工资是多少", ALIASES)[0]["status"]
        == "no_confirmed_row"
    )


def test_null_counts_and_overlapping_rows_preserve_sample_scope():
    items = library()
    null, _ = table_plan(items, {"student-data"}, "学生80001的成绩与学分？", ALIASES)
    assert (
        next(r for r in null["rows"] if r["table"] == "takes")["values"]["grade"]
        == "null"
    )
    duplicate = Candidate(
        Chunk(
            id="duplicate",
            text=next(x.chunk.text for x in items if x.chunk.id == "student-data"),
            chunk_index=99,
            page_start=6,
        ),
        Document(id="test-book"),
    )
    count, _ = table_plan(
        [*items, duplicate], {"student-data"}, "student样例有多少条？"
    )
    assert count["counts"][0]["count"] == 2
    assert count["counts"][0]["scope"] == "complete_parsed_sample_table_only"
    assert "不是全校实际总数" in render_verified(count, [*items, duplicate])


def test_ambiguous_conflicting_rows_and_undeclared_links_fail_closed():
    items = library()
    teacher = next(x for x in items if x.chunk.id == "instructor-data")
    teacher.chunk.text += "\n70001 Other Computing 60000"
    catalog = Catalog(items)
    assert "instructor" in catalog.bad_tables and not catalog.rows.get("instructor")
    ambiguous = [
        Candidate(
            Chunk(
                id="a",
                text="create table example\n(a varchar(20),\nb varchar(20),\n"
                "primary key(a));",
                chunk_index=0,
            ),
            Document(id="d"),
        ),
        Candidate(
            Chunk(
                id="b",
                text="Figure B.1 The example relation.\na b\nx y z",
                chunk_index=1,
            ),
            Document(id="d"),
        ),
    ]
    assert "example" in Catalog(ambiguous).bad_tables
    items = library()
    next(
        x for x in items if x.chunk.id == "advisor-ddl"
    ).chunk.text = (
        "create table advisor\n(s_ID varchar(5),\ni_ID varchar(5),\nprimary key(s_ID));"
    )
    plan, _ = table_plan(items, {"advisor-data"}, "advisor 80001 instructor姓名？")
    assert not any(r["table"] == "instructor" for r in plan["rows"])
    assert cell_key("A_B") != cell_key("AB") != cell_key("A B")


def test_bounded_rows_and_hops():
    plan, _ = table_plan(
        library(),
        {"section-data"},
        "LAB-42 Spring 2025 section 1教师教室？",
        ALIASES,
        max_rows=1,
    )
    assert plan["status"] == "row_limit"
    plan, _ = table_plan(
        library(),
        {"section-data"},
        "LAB-42 Spring 2025 section 1教师教室？",
        ALIASES,
        max_hops=1,
    )
    assert not any(r["table"] == "instructor" for r in plan["rows"])
    items = library()
    items[0].document.id = "different-book"
    assert (
        table_plan(items, {"section-data"}, "LAB-42教师？", ALIASES)[0]["status"]
        == "cross_document_join_blocked"
    )


def test_chinese_adjacent_conditions_paired_terms_short_names_and_no_unbacked_keys():
    from app.table_evidence import Row, row_filters

    def sample(season, year):
        return Row(
            "section",
            {"courseid": "LAB-42", "semester": season, "year": year, "secid": "1"},
        )

    query = "LAB-42在Fall 2025的section 1教师？"
    assert row_filters(sample("Fall", "2025"), query)
    assert not row_filters(sample("Spring", "2025"), query)
    assert not row_filters(sample("Fall", "2024"), query)
    compare = "LAB-42在Fall 2024与Spring 2025的成绩"
    assert row_filters(sample("Fall", "2024"), compare)
    assert not row_filters(sample("Fall", "2025"), compare)
    assert not row_filters(sample("Spring", "2024"), compare)
    items = library()
    next(x for x in items if x.chunk.id == "instructor-data").chunk.text = next(
        x.chunk.text for x in items if x.chunk.id == "instructor-data"
    ).replace("Ada", "Ng")
    plan, ids = table_plan(items, {"instructor-data"}, "教师Ng的工资预算？", ALIASES)
    assert any(r["table"] == "department" for r in plan["rows"])
    assert not plan["keys"]
    text = render_verified(plan, [x for x in items if x.chunk.id in ids])
    assert "主键" not in text
    assert cell_key("A") != cell_key("a")


def test_precise_ddl_provenance_and_explicit_missing_field_refusal():
    items = library()
    catalog = Catalog(items)
    assert catalog.schemas["instructor"].sources == {"instructor-ddl"}
    assert catalog.schemas["student"].sources == {"student-ddl"}
    plan, ids = table_plan(
        items, {"instructor-data"}, "Ada的奖金与电话号码是多少？", ALIASES
    )
    text = render_verified(plan, [i for i in items if i.chunk.id in ids])
    assert set(plan["unverified_items"]) == {"奖金", "电话号码"}
    assert "无法回答" in text and "不能用其他字段的数值替代" in text
    positive, _ = table_plan(items, {"instructor-data"}, "Ada的工资和预算？", ALIASES)
    assert not positive["unverified_items"]


def test_supplement_proof_budget_is_all_or_nothing():
    from app.core.config import Settings
    from app.retrieval import RetrievalResult, supplement_tables

    items = library()

    class Statement:
        def where(self, *_):
            return self

        def order_by(self, *_):
            return self

        def limit(self, *_):
            return self

    store = SimpleNamespace(
        statement=lambda: Statement(),
        db=SimpleNamespace(
            execute=lambda _: SimpleNamespace(
                all=lambda: [(x.chunk, x.document) for x in items]
            )
        ),
    )
    settings = Settings(_env_file=None, retrieval_table_max_sources=1)
    result = RetrievalResult(
        "rerank", evidence=[next(x for x in items if x.chunk.id == "instructor-data")]
    )
    supplement_tables(store, result, "Ada的预算是多少？", settings)
    assert result.table_verification["documents"][0]["status"] == "proof_context_limit"
    assert len(result.evidence) == 1 and not result.branches
    settings.retrieval_table_scan_chunks = 1
    supplement_tables(store, result, "Ada的预算是多少？", settings)
    assert result.table_verification["status"] == "scan_limit"


def test_table_lookup_live_pgvector_scope_and_deletion(monkeypatch, tmp_path):
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.db.models import ChatSession
    from app.db.session import get_engine
    from app.evaluation import fixtures as module
    from app.ingestion.parser import Part
    from app.retrieval import search

    vector = [1.0] + [0.0] * 511
    monkeypatch.setattr(module, "embed_texts", lambda texts: [vector for _ in texts])
    monkeypatch.setattr(
        module,
        "parse_document",
        lambda *_: [
            Part(item.chunk.text, page=i + 1) for i, item in enumerate(library())
        ],
    )
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "generated.md").write_text(
        "self-authored", encoding="utf-8"
    )
    specs = [
        ("allowed", "a", "main", "ready"),
        ("foreign", "b", "main", "ready"),
        ("other", "a", "other", "ready"),
        ("deleted", "a", "main", "deleted"),
        ("processing", "a", "main", "processing"),
    ]
    dataset = {
        "documents": [
            dict(key=k, owner=o, course=c, status=s, file="generated.md")
            for k, o, c, s in specs
        ]
    }
    settings = get_settings().model_copy(deep=True)
    settings.retrieval_strategy = "dense"
    settings.retrieval_verified_tables = True
    with module.fixtures(
        dataset, tmp_path, 900, 150, tmp_path / "dataset.json", pdf_layout=True
    ) as (owners, courses, docs):
        scope = ChatSession(
            owner_id=owners["a"],
            course_id=courses["a", "main"],
            document_ids=[docs["allowed"]],
        )
        with Session(get_engine()) as db:

            def run():
                return search(
                    db,
                    scope,
                    "Ada的工资和预算是多少？",
                    settings,
                    embed=lambda _: [vector],
                )

            result = run()
            assert (
                result.table_verification["documents"][0]["status"] == "verified_rows"
            )
            assert all(x.document.id == docs["allowed"] for x in result.evidence)
            assert all(
                x.chunk.owner_id == owners["a"]
                for x in result.branches["verified_key_lookup"]
            )
            # Independent exact-row gate can rescue a recalled row rejected by
            # relevance scoring; the numeric rerank gate remains untouched.
            settings.retrieval_strategy = "rerank"
            settings.retrieval_rerank_gate = "score"
            settings.retrieval_rerank_min_score = 5
            scorer = SimpleNamespace(score=lambda q, texts: [-100.0] * len(texts))
            rescued = search(
                db,
                scope,
                "Ada的工资和预算是多少？",
                settings,
                embed=lambda _: [vector],
                reranker=scorer,
            )
            assert rescued.evidence
            assert not rescued.table_verification["base_evidence_ids"]
            assert (
                "rerank_threshold"
                in rescued.table_verification["base_rejected"].values()
            )
            assert set(rescued.table_verification["base_rejected"].values()) <= {
                "rerank_threshold",
                "outside_top_k",
            }
            assert settings.retrieval_rerank_min_score == 5
            settings.retrieval_strategy = "dense"
            for key in ("foreign", "other", "deleted", "processing"):
                scope.document_ids = [docs[key]]
                assert not run().evidence
            scope.document_ids = [docs["allowed"]]
            db.get(Document, docs["allowed"]).status = "deleted"
            db.commit()
            assert not run().evidence


def test_deterministic_audit_detects_wrong_value_and_join_key():
    from copy import deepcopy

    from app.evaluation.table_checks import check_case

    items = library()
    plan, _ = table_plan(
        items, {"teaches-data"}, "LAB-42 Spring 2025 section 1的教师姓名？", ALIASES
    )
    row = dict(
        id="synthetic",
        strategy="rerank",
        answer_method="verified_table_v1",
        answer=render_verified(plan, items),
        actual_context=[{"chunk_id": i.chunk.id, "text": i.chunk.text} for i in items],
        trace={"table_verification": {"documents": [plan]}},
    )
    gold = [dict(table="instructor", where={"id": "70001"}, values={"name": "Ada"})]
    assert check_case(row, gold)["status"] == "pass"
    bad = deepcopy(row)
    bad["trace"]["table_verification"]["documents"][0]["joins"][0]["matches"][0][
        "to_values"
    ]["id"] = "99999"
    assert "invalid_complete_join_key" in check_case(bad, gold)["errors"]
    gold[0]["values"]["name"] = "Ben"
    assert check_case(row, gold)["status"] == "fail"
