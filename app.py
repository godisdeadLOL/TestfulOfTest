from fastapi import FastAPI, HTTPException, Request, Depends, Security
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from slowapi import Limiter
from slowapi.util import get_ipaddr

import httpx, os, json

from utils import OpenaiException, ProxyResponse, generate_openai_error, is_stream_request, trim_key

# import dotenv
# dotenv.load_dotenv()

app = FastAPI()
security = HTTPBearer()
limiter = Limiter(key_func=get_ipaddr)

app.state.counter = 0
app.state.keys = [ [key, 'ok'] for key in os.environ["API_KEYS"].split(',') ]

@app.exception_handler(429)
async def rate_limit_error(request: Request, _) :
    stream = await is_stream_request(request)
    return ProxyResponse("Rate limit", stream=stream, status_code=203)

def next_key() :
    keys = app.state.keys
    
    for _ in range( len(keys) ) :
        app.state.counter = (app.state.counter+1) % len(keys)
        
        key, status = keys[app.state.counter]
        if status == 'ok' :
            print("Using key:", trim_key(key))
            return key, app.state.counter
        
    return None, None

def update_key_status(key_index : int, status : str) :
    keys = app.state.keys
    keys[key_index][1] = status
    
    key, status = keys[key_index]
    print(trim_key(key), '-', status)

@app.exception_handler(OpenaiException)
async def handle_openai_error (request: Request, exc: OpenaiException) :
    code = exc.body.get('error', {}).get('code', None)
    if code == None : return ProxyResponse(str(exc.body), stream=exc.stream)
    
    if not code in ('empty_array', 'invalid_type', 'invalid_value', 'model_not_found', 'unsupported_country_region_territory') :
        update_key_status(exc.key_index, code)
    
    key = trim_key(app.state.keys[exc.key_index][0])
    return ProxyResponse(f"{key} - {code}", stream=exc.stream, status_code=203)

@app.exception_handler(httpx.TimeoutException)
async def handle_timeout_error (request: Request, _) :
    stream = await is_stream_request(request)
    return ProxyResponse("Request timeout", stream=stream, status_code=203)
    
def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    if credentials.credentials != os.environ['TOKEN'] :
        raise HTTPException(status_code=401, detail="Wrong token")

@app.post("/chat/completions")
@limiter.limit(os.environ['RATE_LIMIT'])
async def completions(request: Request, token=Depends(verify_token)) :
    data = await request.json()
    stream = data.get("stream", False)
    
    # if random() > .9 : data['model'] = "gpt-3.5-turbo"

    client = httpx.AsyncClient()
    
    max_retries = int(os.environ['MAX_RETRIES'])
    for i in range(max_retries) :
        key, key_index = next_key()
        if key == None : return ProxyResponse(f"No more keys...", stream=stream, status_code=203)
        
        headers = { "Authorization" : f"Bearer {key}" }
        
        try :
            req = client.build_request("POST", "https://api.openai.com/v1/chat/completions", json=data, headers=headers, timeout=10)
            response : httpx.Response = await client.send(req, stream=True)
        except httpx.TimeoutException as exc :
            if i == (max_retries-1) : raise exc
            continue
        
        if response.status_code == 200 : break
        await handle_openai_error(request, await generate_openai_error(request, response, key_index))
    
    if response.status_code != 200 :
        raise await generate_openai_error(request, response, key_index)
    
    async def event_generator() :
        try :
            async for chunk in response.aiter_text() :
                yield chunk
        finally :
            await response.aclose()
            await client.aclose()
        
    if stream :
        return StreamingResponse(event_generator(), media_type="text/event-stream")
    else :
        content = await response.aread()
        return PlainTextResponse(content.decode(), media_type="application/json")

@app.get("/models")
async def models(token=Depends(verify_token)) :
    models = [ {'id' : model} for model in os.environ['MODELS'].split(',') ]
    
    return JSONResponse({'data' : models})

@app.get("/")
async def index() :
    return PlainTextResponse("lol")