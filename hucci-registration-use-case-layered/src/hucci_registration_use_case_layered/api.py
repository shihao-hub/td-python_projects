import sys

from fastapi import APIRouter, Depends, Form, HTTPException

from .domain import AlreadyEnrolledError, CourseFullError
from .service import EnrollService

# 推荐在此处设置公共前缀 prefix 和标签 tags
router = APIRouter()


def get_enroll() -> EnrollService:
    raise NotImplementedError


@router.post("/enroll")
def enroll(
    student_id: str = Form(),
    course_id: str = Form(),
    enroll_service: EnrollService = Depends(get_enroll),
):
    try:
        enroll_service.enroll(student_id, course_id)
        return {"message": "报名成功"}
    except CourseFullError as exc:
        raise HTTPException(status_code=400, detail="名额已满") from exc
    except AlreadyEnrolledError as exc:
        raise HTTPException(status_code=400, detail="请勿重复报名") from exc
    except Exception as exc:
        print(f"{type(exc)} -> {exc}", file=sys.stderr)
        raise HTTPException(status_code=500, detail="出现未知错误，具体查看日志")
