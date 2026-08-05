from fastapi import FastAPI

app = FastAPI(title="{{project_name}}")


@app.get("/api/hello")
def hello() -> dict[str, str]:
    return {"message": "hello from {{project_name}}"}
