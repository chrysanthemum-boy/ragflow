#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License
#
import os
import pathlib

from flask import request
from flask_login import login_required, current_user
from elasticsearch_dsl import Q
from rag.nlp import search
from rag.utils import ELASTICSEARCH
from api.utils.api_utils import server_error_response, get_data_error_result, validate_request
from api.utils import get_uuid
from api.db import FileType
from api.db.services import duplicate_name
from api.db.services.file_service import FileService
from api.settings import RetCode
from api.utils.api_utils import get_json_result
from api.utils.file_utils import filename_type
from rag.utils.minio_conn import MINIO


@manager.route('/upload', methods=['POST'])
@login_required
@validate_request("parent_id")
def upload():
    pf_id = request.form.get("parent_id")
    path = request.form.get("path")
    if not pf_id:
        return get_json_result(
            data=False, retmsg='Lack of "Parent Folder ID"', retcode=RetCode.ARGUMENT_ERROR)

    if 'file' not in request.files:
        return get_json_result(
            data=False, retmsg='No file part!', retcode=RetCode.ARGUMENT_ERROR)
    file_obj = request.files['file']
    if file_obj.filename == '':
        return get_json_result(
            data=False, retmsg='No file selected!', retcode=RetCode.ARGUMENT_ERROR)

    try:
        e, file = FileService.get_by_id(pf_id)
        if not e:
            return get_data_error_result(
                retmsg="Can't find this folder!")
        if FileService.get_file_count(file.tenant_id) >= int(os.environ.get('MAX_FILE_NUM_PER_USER', 8192)):
            return get_data_error_result(
                retmsg="Exceed the maximum file number of a free user!")

        # split file name path
        file_obj_names = path.split('/')
        file_len = len(file_obj_names)

        # get folder
        file_id_list = FileService.get_id_list_by_id(pf_id, file_obj_names, 1, [pf_id])
        len_id_list = len(file_id_list)

        # create folder
        if file_len != len_id_list:
            e, file = FileService.get_by_id(file_id_list[len_id_list - 1])
            if not e:
                return get_data_error_result(retmsg="Folder not found!")
            last_folder = FileService.create_folder(file, file_id_list[len_id_list - 1], file_obj_names, len_id_list)
        else:
            e, file = FileService.get_by_id(file_id_list[len_id_list - 2])
            if not e:
                return get_data_error_result(retmsg="Folder not found!")
            last_folder = FileService.create_folder(file, file_id_list[len_id_list - 2], file_obj_names, len_id_list)

        # file type
        filetype = filename_type(file_obj_names[file_len - 1])
        if not filetype:
            return get_data_error_result(
                retmsg="This type of file has not been supported yet!")

        location = file_obj_names[file_len - 1]
        while MINIO.obj_exist(last_folder.id, location):
            location += "_"
        blob = request.files['file'].read()
        MINIO.put(last_folder.id, location, blob)
        filename = duplicate_name(
            FileService.query,
            name=file_obj_names[file_len - 1],
            parent_id=last_folder.id)
        file = {
            "id": get_uuid(),
            "parent_id": last_folder.id,
            "tenant_id": current_user.id,
            "created_by": current_user.id,
            "type": filetype,
            "name": filename,
            "location": location,
            "size": len(blob),
        }
        file = FileService.insert(file)
        return get_json_result(data=file.to_json())
    except Exception as e:
        return server_error_response(e)


@manager.route('/create', methods=['POST'])
@login_required
@validate_request("name", "parent_id")
def create():
    req = request.json
    pf_id = req["parent_id"]
    input_file_type = request.json.get("type")
    if not pf_id:
        return get_json_result(
            data=False, retmsg='Lack of "Parent Folder ID"', retcode=RetCode.ARGUMENT_ERROR)

    try:
        if not FileService.is_parent_folder_exist(pf_id):
            return get_json_result(
                data=False, retmsg="Parent Folder Doesn't Exist!", retcode=RetCode.OPERATING_ERROR)
        if FileService.query(name=req["name"], parent_id=pf_id):
            return get_data_error_result(
                retmsg="Duplicated folder name in the same folder.")

        if input_file_type == FileType.FOLDER.value:
            file_type = FileType.FOLDER
        else:
            file_type = FileType.VIRTUAL

        file = FileService.insert({
            "id": get_uuid(),
            "parent_id": pf_id,
            "tenant_id": current_user.id,
            "created_by": current_user.id,
            "name": req["name"],
            "location": "",
            "size": 0,
            "type": file_type
        })

        return get_json_result(data=file.to_json())
    except Exception as e:
        return server_error_response(e)


@manager.route('/list', methods=['GET'])
@login_required
def list():
    pf_id = request.args.get("parent_id")
    if not pf_id:
        return get_json_result(
            data=False, retmsg='Lack of "Parent Folder ID"', retcode=RetCode.ARGUMENT_ERROR)
    keywords = request.args.get("keywords", "")

    page_number = int(request.args.get("page", 1))
    items_per_page = int(request.args.get("page_size", 15))
    orderby = request.args.get("orderby", "create_time")
    desc = request.args.get("desc", True)
    try:
        e, file = FileService.get_by_id(pf_id)
        if not e:
            return get_data_error_result(retmsg="Folder not found!")

        files, total = FileService.get_by_pf_id(
            pf_id, page_number, items_per_page, orderby, desc, keywords)

        parent_folder = FileService.get_parent_folder(pf_id)
        if not FileService.get_parent_folder(pf_id):
            return get_json_result(retmsg="File not found!")

        return get_json_result(data={"total": total, "files": files, "parent_folder": parent_folder.to_json()})
    except Exception as e:
        return server_error_response(e)


@manager.route('/root_folder', methods=['GET'])
@login_required
def get_root_folder():
    try:
        root_folder = FileService.get_root_folder(current_user.id)
        return get_json_result(data={"root_folder": root_folder.to_json()})
    except Exception as e:
        return server_error_response(e)


@manager.route('/parent_folder', methods=['GET'])
@login_required
def get_parent_folder():
    file_id = request.args.get("file_id")
    try:
        e, file = FileService.get_by_id(file_id)
        if not e:
            return get_data_error_result(retmsg="Folder not found!")

        parent_folder = FileService.get_parent_folder(file_id)
        return get_json_result(data={"parent_folder": parent_folder.to_json()})
    except Exception as e:
        return server_error_response(e)


@manager.route('/rm', methods=['POST'])
@login_required
@validate_request("file_ids")
def rm():
    req = request.json
    file_ids = req["file_ids"]
    try:
        for file_id in file_ids:
            e, file = FileService.get_by_id(file_id)
            if not e:
                return get_data_error_result(retmsg="File or Folder not found!")
            if not file.tenant_id:
                return get_data_error_result(retmsg="Tenant not found!")

            if file.type == FileType.FOLDER:
                file_id_list = FileService.get_all_innermost_file_ids(file_id, [])
                for inner_file_id in file_id_list:
                    e, file = FileService.get_by_id(inner_file_id)
                    if not e:
                        return get_data_error_result(retmsg="File not found!")
                    MINIO.rm(file.parent_id, file.location)
                FileService.delete_folder_by_pf_id(file_id)
            else:
                if not FileService.delete(file):
                    return get_data_error_result(
                        retmsg="Database error (File removal)!")

        return get_json_result(data=True)
    except Exception as e:
        return server_error_response(e)


@manager.route('/rename', methods=['POST'])
@login_required
@validate_request("file_id", "name")
def rename():
    req = request.json
    try:
        e, file = FileService.get_by_id(req["file_id"])
        if not e:
            return get_data_error_result(retmsg="File not found!")
        if pathlib.Path(req["name"].lower()).suffix != pathlib.Path(
                file.name.lower()).suffix:
            return get_json_result(
                data=False,
                retmsg="The extension of file can't be changed",
                retcode=RetCode.ARGUMENT_ERROR)
        if FileService.query(name=req["name"], pf_id=file.parent_id):
            return get_data_error_result(
                retmsg="Duplicated file name in the same folder.")

        if not FileService.update_by_id(
                req["file_id"], {"name": req["name"]}):
            return get_data_error_result(
                retmsg="Database error (File rename)!")

        return get_json_result(data=True)
    except Exception as e:
        return server_error_response(e)
