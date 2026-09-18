from app.db.models import Project
from app.db.session import SessionLocal


def test_db_initializes_and_can_persist_a_project():
    with SessionLocal() as db:
        project = Project(name="Sample Gantry Site", workspace_path="/workspace/sample")
        db.add(project)
        db.commit()
        db.refresh(project)

        assert project.id is not None
        assert project.class_schema_version == "atcc-v1"

        fetched = db.get(Project, project.id)
        assert fetched is not None
        assert fetched.name == "Sample Gantry Site"
