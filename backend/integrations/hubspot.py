# hubspot.py

import json
import secrets
from urllib.parse import urlencode

import asyncio
import httpx
import requests
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from integrations.integration_item import IntegrationItem
from redis_client import add_key_value_redis, delete_key_redis, get_value_redis

CLIENT_ID = 'XXX'
CLIENT_SECRET = 'XXX'
REDIRECT_URI = 'http://localhost:8000/integrations/hubspot/oauth2callback'
authorization_url = 'https://app.hubspot.com/oauth/authorize'
scope = 'crm.objects.contacts.read crm.objects.companies.read'


async def authorize_hubspot(user_id, org_id):
    state_data = {
        'state': secrets.token_urlsafe(32),
        'user_id': user_id,
        'org_id': org_id,
    }
    encoded_state = json.dumps(state_data)
    await add_key_value_redis(f'hubspot_state:{org_id}:{user_id}', encoded_state, expire=600)

    params = {
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'scope': scope,
        'state': encoded_state,
    }

    return f'{authorization_url}?{urlencode(params)}'


async def oauth2callback_hubspot(request: Request):
    if request.query_params.get('error'):
        raise HTTPException(status_code=400, detail=request.query_params.get('error_description') or request.query_params.get('error'))

    code = request.query_params.get('code')
    if not code:
        raise HTTPException(status_code=400, detail='Missing authorization code.')

    encoded_state = request.query_params.get('state')
    if not encoded_state:
        raise HTTPException(status_code=400, detail='Missing state.')

    state_data = json.loads(encoded_state)
    original_state = state_data.get('state')
    user_id = state_data.get('user_id')
    org_id = state_data.get('org_id')

    saved_state = await get_value_redis(f'hubspot_state:{org_id}:{user_id}')
    if not saved_state or original_state != json.loads(saved_state).get('state'):
        raise HTTPException(status_code=400, detail='State does not match.')

    async with httpx.AsyncClient() as client:
        response, _ = await asyncio.gather(
            client.post(
                'https://api.hubapi.com/oauth/v1/token',
                data={
                    'grant_type': 'authorization_code',
                    'client_id': CLIENT_ID,
                    'client_secret': CLIENT_SECRET,
                    'redirect_uri': REDIRECT_URI,
                    'code': code,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            ),
            delete_key_redis(f'hubspot_state:{org_id}:{user_id}'),
        )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    await add_key_value_redis(
        f'hubspot_credentials:{org_id}:{user_id}',
        json.dumps(response.json()),
        expire=600,
    )

    close_window_script = """
    <html>
        <script>
            window.close();
        </script>
    </html>
    """
    return HTMLResponse(content=close_window_script)


async def get_hubspot_credentials(user_id, org_id):
    credentials = await get_value_redis(f'hubspot_credentials:{org_id}:{user_id}')
    if not credentials:
        raise HTTPException(status_code=400, detail='No credentials found.')

    credentials = json.loads(credentials)
    await delete_key_redis(f'hubspot_credentials:{org_id}:{user_id}')
    return credentials


def create_integration_item_metadata_object(response_json, item_type) -> IntegrationItem:
    properties = response_json.get('properties', {})
    if item_type == 'Contact':
        first_name = properties.get('firstname') or ''
        last_name = properties.get('lastname') or ''
        full_name = f'{first_name} {last_name}'.strip()
        name = full_name or properties.get('email') or 'Unnamed'
    else:
        name = properties.get('name') or 'Unnamed'

    return IntegrationItem(
        id=f"{response_json.get('id')}_{item_type}",
        name=name,
        type=item_type,
    )


async def get_items_hubspot(credentials) -> list[IntegrationItem]:
    credentials = json.loads(credentials)
    access_token = credentials.get('access_token')
    if not access_token:
        raise HTTPException(status_code=400, detail='Missing access token.')

    headers = {'Authorization': f'Bearer {access_token}'}
    endpoints = [
        ('https://api.hubapi.com/crm/v3/objects/contacts', 'Contact'),
        ('https://api.hubapi.com/crm/v3/objects/companies', 'Company'),
    ]

    list_of_integration_item_metadata = []
    for url, item_type in endpoints:
        response = requests.get(url, headers=headers)
        if response.status_code == 401:
            raise HTTPException(status_code=401, detail='HubSpot token expired or unauthorized.')
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)

        results = response.json().get('results', [])
        for result in results:
            list_of_integration_item_metadata.append(
                create_integration_item_metadata_object(result, item_type)
            )

    return list_of_integration_item_metadata
