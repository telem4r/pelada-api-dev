from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse


def error_payload(*, code: str, message: str, details: Any | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "details": details or {},
    }


def api_error(status_code: int, *, code: str, message: str, details: Any | None = None) -> HTTPException:
    return HTTPException(status_code=status_code, detail=error_payload(code=code, message=message, details=details))


def error_response(status_code: int, *, code: str, message: str, details: Any | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error_payload(code=code, message=message, details=details))


def normalize_http_message(status_code: int, raw_message: str | None = None) -> str:
    text = (raw_message or '').strip()
    lower = text.lower()

    if status_code == 400:
        if lower:
            return text
        return 'Não foi possível concluir a operação com os dados informados.'
    if status_code == 401:
        return 'A sua sessão expirou. Entre novamente para continuar.'
    if status_code == 403:
        if 'admin' in lower or 'owner' in lower or 'permission' in lower or 'permiss' in lower:
            return 'Você não tem permissão para executar esta ação.'
        return 'Esta ação não está disponível para o seu perfil.'
    if status_code == 404:
        if 'sorteio salvo' in lower or 'ainda não existe sorteio salvo' in lower or 'draw/result' in lower or 'draw result' in lower:
            return 'Ainda não existe sorteio salvo para esta partida.'
        if 'match' in lower or 'partida' in lower:
            return 'A partida já não está disponível. Atualize a tela e tente novamente.'
        if 'group' in lower or 'grupo' in lower:
            return 'O grupo já não está disponível. Atualize a tela e tente novamente.'
        return 'O conteúdo pedido não foi encontrado.'
    if status_code == 409:
        return text or 'A operação não pode ser concluída no estado atual. Atualize a tela e tente novamente.'
    if status_code == 422:
        return text or 'Os dados enviados são inválidos. Revise as informações e tente novamente.'
    if status_code >= 500:
        return 'Ocorreu uma falha interna. Tente novamente em instantes.'
    return text or 'Não foi possível concluir a operação agora.'


def with_request_id(details: Any | None, request_id: str | None) -> dict[str, Any]:
    payload = dict(details or {}) if isinstance(details, dict) else ({"raw": details} if details is not None else {})
    if request_id:
        payload.setdefault("request_id", request_id)
    return payload
