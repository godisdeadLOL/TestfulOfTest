import json

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

class OpenaiException(Exception) :
    def __init__ (self, status_code : int, body, stream, key_index : int) :
        self.status_code : int = status_code
        self.body : dict = body
        self.stream : bool = stream
        self.key_index : int = key_index

def trim_key(key : str) :
    return "***" + key[-6:]

def ProxyResponse(text: str, stream: bool, status_code: int = 200) :
    res = f"### PROXY RESPONSE:\n```\n{text}\n```"

    if stream :
        data = json.dumps( { 'choices' : [{ 'delta' : { 'content' : res } }] } )
        return PlainTextResponse(f"data: {data}\n\n[DONE]", media_type="text/event-stream", status_code=status_code)
    else: return JSONResponse({'choices' : [ {'message': {'content': res} } ]}, status_code=status_code)
    
async def is_stream_request(request) :
    try : 
        data = await request.json()
        stream = data.get('stream', False)
    except : stream = False
    
    return stream
    
async def generate_openai_error(request, response, key_index) :
    stream = await is_stream_request(request)
    
    content = await response.aread()
    
    try : body = json.loads(content.decode())
    except : body = {}
    
    return OpenaiException(response.status_code, body, stream, key_index)