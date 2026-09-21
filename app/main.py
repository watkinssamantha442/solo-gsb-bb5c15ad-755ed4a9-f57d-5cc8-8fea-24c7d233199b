"""FastAPI 入口：单条诊断、修复建议、批量诊断与健康检查。"""

from __future__ import annotations

from typing import List

from fastapi import FastAPI

from .models import (
    BatchDiagnosisRequest,
    BatchDiagnosisResponse,
    BatchItemResponse,
    ContradictionCycle,
    CycleStep,
    DiagnosisRequest,
    DiagnosisResponse,
    FeasibleDiagnosis,
    InfeasibleDiagnosis,
    RepairRequest,
    RepairResponse,
)
from .solver import Observation, diagnose, find_minimal_repair, is_feasible

app = FastAPI(
    title="分布式时钟约束诊断 API",
    description=(
        "提交形如「B 时钟 - A 时钟 ∈ [min, max] 毫秒」的有向观测，"
        "服务将其转换为差分约束，判断时钟偏移是否一致；"
        "可行时给出相对参考节点的最紧上下界，不可行时返回稳定的矛盾环，"
        "并可搜索使系统恢复可行的最小观测删除集（修复建议）。"
    ),
    version="1.1.0",
)


def _to_observation(raw) -> Observation:
    return Observation(
        id=raw.id,
        source=raw.source,
        target=raw.target,
        min=raw.min,
        max=raw.max,
    )


def _run_diagnosis(request: DiagnosisRequest):
    result = diagnose(
        nodes=request.effective_nodes(),
        reference=request.reference,
        observations=[_to_observation(o) for o in request.observations],
    )
    if result["feasible"]:
        return FeasibleDiagnosis(name=request.name, **result)

    cycle = result["cycle"]
    steps: List[CycleStep] = [
        CycleStep(
            observation_id=s["observation_id"],
            from_=s["from"],
            to=s["to"],
            constraint=s["constraint"],
            weight=s["weight"],
            bound=s["bound"],
        )
        for s in cycle["steps"]
    ]
    return InfeasibleDiagnosis(
        name=request.name,
        cycle=ContradictionCycle(
            steps=steps,
            accumulated_bound=cycle["accumulated_bound"],
            contradiction=cycle["contradiction"],
        ),
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/diagnose", response_model=DiagnosisResponse, tags=["diagnosis"])
def diagnose_once(request: DiagnosisRequest) -> DiagnosisResponse:
    """提交一组观测与参考节点，返回单次诊断结果。"""
    return _run_diagnosis(request)


@app.post(
    "/api/v1/diagnose/repair",
    response_model=RepairResponse,
    tags=["diagnosis"],
)
def diagnose_repair(request: RepairRequest) -> RepairResponse:
    """不可行时搜索最小观测删除集（只删不改），使剩余系统恢复可行。"""
    nodes = request.effective_nodes()
    observations = [_to_observation(o) for o in request.observations]

    if is_feasible(nodes, observations):
        return RepairResponse(
            feasible=True,
            name=request.name,
            max_repairs=request.max_repairs,
            limit_reached=False,
            deleted_observation_ids=[],
            repaired_diagnosis=None,
        )

    deleted_ids = find_minimal_repair(nodes, observations, request.max_repairs)
    if deleted_ids is None:
        return RepairResponse(
            feasible=False,
            name=request.name,
            max_repairs=request.max_repairs,
            limit_reached=True,
            deleted_observation_ids=[],
            repaired_diagnosis=None,
        )

    deleted = set(deleted_ids)
    remaining = [o for o in observations if o.id not in deleted]
    repaired = diagnose(
        nodes=nodes, reference=request.reference, observations=remaining
    )
    return RepairResponse(
        feasible=False,
        name=request.name,
        max_repairs=request.max_repairs,
        limit_reached=False,
        deleted_observation_ids=deleted_ids,
        repaired_diagnosis=FeasibleDiagnosis(**repaired),
    )


@app.post(
    "/api/v1/diagnose/batch",
    response_model=BatchDiagnosisResponse,
    tags=["diagnosis"],
)
def diagnose_batch(request: BatchDiagnosisRequest) -> BatchDiagnosisResponse:
    """批量诊断；结果按提交顺序（index）稳定返回。"""
    items = [
        BatchItemResponse(index=i, name=req.name, result=_run_diagnosis(req))
        for i, req in enumerate(request.diagnoses)
    ]
    return BatchDiagnosisResponse(count=len(items), results=items)
