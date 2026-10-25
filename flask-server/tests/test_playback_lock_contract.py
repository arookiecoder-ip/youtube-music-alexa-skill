"""Actual non-reentrant-lock checks for maintenance and fallback control paths."""
import ast
import asyncio
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from flask import Flask, request, jsonify

SOURCE = Path(__file__).resolve().parents[1] / 'server.py'
TREE = ast.parse(SOURCE.read_text())


class ProductionLock:
    def __init__(self): self.lock = threading.Lock()
    def __enter__(self):
        if not self.lock.acquire(timeout=.2):
            raise AssertionError('Playback lock re-entered')
        return self
    def __exit__(self, *args): self.lock.release()


class PlaybackLockContractTests(unittest.TestCase):
    def functions(self, names, namespace):
        nodes = [node for node in TREE.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names]
        for node in nodes: node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),namespace)

    def test_thumbnail_backfill_notifies_once_after_releasing_lock(self):
        lock = ProductionLock()
        state = {'queue':[{'video_id':'A','thumbnail':''},{'video_id':'B','thumbnail':'existing'}]}
        notifications=[]
        def notify():
            with lock: notifications.append(True)
        namespace=dict(_np_lock=lock,_now_playing=state,_lookup_video_metadata=lambda _: {'thumbnail':'https://cover'},
            _thumbnail_url=lambda value:value,_notify_sse=notify,time=time,logger=Mock())
        self.functions({'_backfill_queue_thumbnail'},namespace)
        namespace['_backfill_queue_thumbnail']('A')
        self.assertEqual(state['queue'][0]['thumbnail'],'https://cover')
        self.assertEqual(state['queue'][1]['thumbnail'],'existing')
        self.assertEqual(notifications,[True])
        namespace['_backfill_queue_thumbnail']('A')
        self.assertEqual(notifications,[True])

    def test_like_current_song_uses_snapshot_without_wrapping_its_lock(self):
        client=SimpleNamespace(auth_type=object(),rate_song=Mock())
        namespace=dict(_np_lock=ProductionLock(),_now_playing={'video_id':'A'},
            _get_ytmusic_home=lambda:client,_bump_liked_version=Mock(),
            asyncio=asyncio,request=request,jsonify=jsonify,logger=Mock())
        self.functions({'_get_now_playing','alexa_like'},namespace)
        with Flask(__name__).test_request_context('/alexa/like/',method='POST',json={'action':'LIKE'}):
            response=asyncio.run(namespace['alexa_like']())
        self.assertEqual(response.get_json()['video_id'],'A')
        client.rate_song.assert_called_once_with('A','LIKE')

    def test_lock_taking_helpers_are_never_called_inside_playback_lock(self):
        helpers={'_schedule_play_dispatch','_claim_alexa_output','_get_now_playing','_update_now_playing',
            '_notify_sse','_schedule_alexa_queue_sync','_reconcile_buffered_alexa_start'}
        violations=[]
        for node in ast.walk(TREE):
            if isinstance(node,ast.With) and any(isinstance(item.context_expr,ast.Name) and item.context_expr.id=='_np_lock' for item in node.items):
                violations += [(call.lineno,call.func.id) for call in ast.walk(node)
                    if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id in helpers]
        self.assertEqual(violations,[])
