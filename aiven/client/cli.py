# Copyright 2015, Aiven, https://aiven.io/
#
# This file is under the Apache License, Version 2.0.
# See the file `LICENSE` for details.

from __future__ import print_function
from . import argx, client
from aiven.client import envdefault
from aiven.client.cliarg import arg
import errno
import getpass
import json as jsonlib
import os
import requests
import time


PLUGINS = []


try:
    from aiven.admin import plugin as adminplugin  # pylint: disable=import-error,no-name-in-module
    PLUGINS.append(adminplugin)
except ImportError:
    pass

try:
    raw_input_func = raw_input  # pylint: disable=undefined-variable
except NameError:
    # python 3.x
    raw_input_func = input


def convert_str_to_value(schema, str_value):
    if "string" in schema["type"]:
        return str_value
    elif "integer" in schema["type"]:
        return int(str_value, 0)  # automatically convert from '123', '0x123', '0o644', etc.
    elif "number" in schema["type"]:
        return float(str_value)
    elif "boolean" in schema["type"]:
        values = {
            "1": True,
            "0": False,
            "true": True,
            "false": False,
        }
        try:
            return values[str_value]
        except KeyError:
            raise argx.UserError("Invalid boolean value {!r}: expected one of {}"
                                 .format(str_value, ", ".join(values)))
    elif "array" in schema["type"]:
        return [convert_str_to_value(schema["items"], val) for val in str_value.split(",")]
    else:
        raise argx.UserError("Supported for option value type(s) {!r} is unimplemented".format(schema["type"]))


class AivenCLI(argx.CommandLineTool):
    def __init__(self):
        argx.CommandLineTool.__init__(self, "avn")
        self.client = None
        for plugin in PLUGINS:
            plugincli = plugin.ClientPlugin()
            self.extend_commands(plugincli)

    def add_args(self, parser):
        parser.add_argument("--auth-ca", help="CA certificate to use [AIVEN_CA_CERT], default %(default)r",
                            default=envdefault.AIVEN_CA_CERT, metavar="FILE")
        parser.add_argument("--auth-token",
                            help="Client auth token to use [AIVEN_AUTH_TOKEN], [AIVEN_CREDENTIALS_FILE]",
                            default=envdefault.AIVEN_AUTH_TOKEN)
        parser.add_argument("--show-http", help="Show HTTP requests and responses", action="store_true")
        parser.add_argument("--url", help="Server base url default %(default)r",
                            default=envdefault.AIVEN_WEB_URL or "https://api.aiven.io")

    def enter_password(self, prompt, var="AIVEN_PASSWORD", confirm=False):
        """Prompt user for a password"""
        password = os.environ.get(var)
        if password:
            return password

        password = getpass.getpass(prompt)
        if confirm:
            again = getpass.getpass("Confirm password again: ")
            if password != again:
                raise argx.UserError("Passwords do not match")

        return password

    def get_project(self):
        """Return project given as cmdline argument or the default project from config file"""
        if self.args.project:
            return self.args.project
        return self.config.get("default_project")

    @arg.email
    def user_login(self):
        """Login as a user"""
        password = self.enter_password("{}'s Aiven password: ".format(self.args.email))
        result = self.client.authenticate_user(email=self.args.email, password=password)
        self._write_auth_token_file(token=result["token"], email=self.args.email)

    @arg.project
    def data_list(self):
        """List project data files"""
        result = self.client.list_data(project=self.get_project())
        print(result)

    @arg.project
    @arg("filename", help="Name of the file to download", nargs="+")
    def data_download(self):
        """Download a data file from a project"""
        for filename in self.args.filename:
            result = self.client.download_data(project=self.get_project(), filename=filename)
            print(result)

    @arg.project
    @arg("filename", help="Name of the file to upload", nargs="+")
    def data_upload(self):
        """Upload a data file to a project"""
        for filename in self.args.filename:
            result = self.client.upload_data(project=self.get_project(), filename=filename)
            print(result)

    @arg.project
    @arg("filename", help="Name of the file to delete", nargs="+")
    def data_delete(self):
        """Delete a data file from a project"""
        for filename in self.args.filename:
            result = self.client.delete_data(project=self.get_project(), filename=filename)
            print(result)

    @arg.project
    @arg.json
    @arg("-n", "--limit", type=int, default=100, help="Get up to N rows of logs")
    def logs(self):
        """View project logs"""
        msgs = self.client.get_logs(project=self.get_project(), limit=self.args.limit)
        if self.args.json:
            print(jsonlib.dumps(msgs, indent=4, sort_keys=True))
        else:
            for log_msg in msgs:
                print("{time:<27}  {msg}".format(**log_msg))

    @arg.project
    @arg.json
    def cloud_list(self):
        """List cloud types"""
        self.print_response(self.client.get_clouds(project=self.get_project()), json=self.args.json)

    def collect_user_config_options(self, obj_def, prefix=""):
        opts = {}
        for prop, spec in sorted(obj_def.get("properties", {}).items()):
            full_name = prop if not prefix else (prefix + "." + prop)
            if spec["type"] == "object":
                opts.update(self.collect_user_config_options(spec, prefix=full_name))
            else:
                opts[full_name] = spec
        for spec in sorted(obj_def.get("patternProperties", {}).values()):
            full_name = "KEY" if not prefix else (prefix + ".KEY")
            if spec["type"] == "object":
                opts.update(self.collect_user_config_options(spec, prefix=full_name))
            else:
                opts[full_name] = spec
        return opts

    @arg.project
    def service_plans(self):
        """List service types"""
        service_types = self.client.get_service_types(project=self.get_project())
        output = []
        for service_type, prop in service_types.items():
            entry = prop.copy()
            entry["service_type"] = service_type
            output.append(entry)

        for info in sorted(output, key=lambda s: s["description"]):
            print("{} Plans:\n".format(info["description"]))
            for plan in sorted(info["service_plans"], key=lambda p: p["service_plan"]):
                args = "{}:{}".format(plan["service_type"], plan["service_plan"])
                print("    {:<20}  {}".format(args, plan["description"]))

            if not info["service_plans"]:
                print("    (no plans available)")

            print()

    @arg.project
    @arg.json
    @arg.verbose
    def service_types(self):
        """List service types"""
        service_types = self.client.get_service_types(project=self.get_project())
        if self.args.json:
            self.print_response(service_types, json=self.args.json)
            return

        output = []
        for service_type, prop in sorted(service_types.items()):
            entry = prop.copy()
            entry["service_type"] = service_type
            output.append(entry)

        self.print_response(output, json=self.args.json, table_layout=[["service_type", "description"]])

        if not self.args.json and self.args.verbose:
            for service_type, service_def in sorted(service_types.items()):
                print("\nService type {!r} options:".format(service_type))
                options = self.collect_user_config_options(service_def["user_config_schema"])
                if not options:
                    print("  (No configurable options)")
                else:
                    for name, spec in sorted(options.items()):
                        default = spec.get("default")
                        if isinstance(default, list):
                            default = ",".join(default)

                        default_desc = "(default={!r})".format(default) if default is not None else ""
                        type_str = spec["type"]
                        type_str = " or ".join(type_str) if isinstance(type_str, list) else type_str
                        print("  -c {name}=<{type}>  {default}\n"
                              "     => {title}"
                              .format(name=name, type=type_str,
                                      default=default_desc, title=spec["title"]))

    SERVICE_LAYOUT = [["service_name", "service_type", "state", "cloud_name", "plan",
                       "group_list", "create_time", "update_time"]]
    EXT_SERVICE_LAYOUT = ["service_uri", "user_config.*"]

    @arg.project
    @arg("name", nargs="*", default=[], help="Service name")
    @arg.service_type
    @arg("--format", help="Format string for output, e.g. '{service_name} {service_uri}'")
    @arg.verbose
    @arg.json
    def service_list(self):
        """List services"""
        services = self.client.get_services(project=self.get_project())
        if self.args.service_type is not None:
            services = [s for s in services if s["service_type"] == self.args.service_type]
        if self.args.name:
            services = [s for s in services if s["service_name"] in self.args.name]

        layout = self.SERVICE_LAYOUT[:]
        if self.args.verbose:
            layout.extend(self.EXT_SERVICE_LAYOUT)

        self.print_response(services, format=self.args.format, json=self.args.json,
                            table_layout=layout)

    @arg.project
    @arg("name", help="Service name")
    @arg("--format", help="Format string for output, e.g. '{service_name} {service_uri}'")
    @arg.verbose
    @arg.json
    def service_get(self):
        """Show a single service"""
        service = self.client.get_service(project=self.get_project(), service_name=self.args.name)

        layout = self.SERVICE_LAYOUT[:]
        if self.args.verbose:
            layout.extend(self.EXT_SERVICE_LAYOUT)

        self.print_response(service, format=self.args.format, json=self.args.json,
                            table_layout=layout, single_item=True)

    @arg.project
    @arg("name", help="Service name")
    @arg("--format", help="Format string for output, e.g. '{service_name} {service_uri}'")
    @arg.verbose
    @arg.json
    def service_credentials_reset(self):
        """Reset service credentials"""
        service = self.client.reset_service_credentials(project=self.get_project(), service=self.args.name)
        layout = [["service_name", "service_type", "state", "cloud_name", "plan",
                   "group_list", "create_time", "update_time"]]
        if self.args.verbose:
            layout.extend(["service_uri", "user_config.*"])
        self.print_response([service], format=self.args.format, json=self.args.json, table_layout=layout)

    @arg.project
    @arg("name", help="Service name")
    @arg("--format", help="Format string for output, e.g. '{calls} {total_time}'")
    @arg.verbose
    @arg.json
    def service_queries_reset(self):
        """Reset PostgreSQL service query statistics"""
        queries = self.client.get_pg_service_query_stats_reset(project=self.get_project(), service=self.args.name)
        self.print_response(queries, format=self.args.format, json=self.args.json)

    @arg.project
    @arg("name", help="Service name")
    @arg("--format", help="Format string for output, e.g. '{calls} {total_time}'")
    @arg.verbose
    @arg.json
    def service_queries(self):
        """List PostgreSQL service query statistics"""
        queries = self.client.get_pg_service_query_stats(project=self.get_project(), service=self.args.name)
        layout = [["query", "max_time", "stddev_time", "min_time", "mean_time", "rows", "calls", "total_time"]]
        if self.args.verbose:
            layout.extend(["dbid", "userid", "queryid", "shared_blks_read", "local_blks_read", "local_blks_hit",
                           "local_blks_written", "local_blks_dirtied", "shared_blks_hit",
                           "shared_blks_dirtied", "shared_blks_written",
                           "blk_read_time", "blk_write_time", "temp_blks_read", "temp_blks_written"])
        self.print_response(queries, format=self.args.format, json=self.args.json, table_layout=layout)

    @arg.project
    @arg("service", nargs="+", help="Service to wait for")
    @arg.timeout
    def service_wait(self):
        """Wait service to reach the 'RUNNING' state"""
        start_time = time.time()
        report_interval = 30.0
        next_report = start_time + report_interval
        last = {}
        while True:
            all_running = True
            for service in self.args.service:
                info = self.client.get_service(project=self.get_project(), service_name=service)
                if info["state"] != last.get(service):
                    self.log.info("Service %r state is now %r", service, info["state"])
                last[service] = info["state"]
                if info["state"] != "RUNNING":
                    all_running = False

            if all_running:
                self.log.info("Service(s) RUNNING: %s", ", ".join(self.args.service))
                return

            if self.args.timeout is not None and (time.time() - start_time) > self.args.timeout:
                self.log.error("Timeout waiting for service(s) to start")
                return 1

            if time.time() >= next_report:
                next_report = time.time() + report_interval
                self.log.info("Waiting for services to start")

            time.sleep(3.0)

    @arg.project
    @arg.force
    @arg("name", help="Service name", nargs="+")
    def service_terminate(self):
        """Terminate service"""
        if not self.args.force and os.environ.get("AIVEN_FORCE") != "true":
            output = [
                "Please re-enter the service name(s) to confirm the service termination.",
                "This cannot be undone and all the data in the service will be lost!",
                "Re-entering service name(s) can be skipped with the --force option.",
            ]
            longest = max(len(line) for line in output)
            print("*" * longest)
            for line in output:
                print(line)
            print("*" * longest)

            for name in self.args.name:
                user_input = raw_input_func("Re-enter service name {!r} for immediate termination: ".format(name))
                if user_input != name:
                    raise argx.UserError("Not confirmed by user. Aborting termination.")

        for name in self.args.name:
            self.client.delete_service(project=self.get_project(), service=name)
            self.log.info("%s: terminated", name)

    def create_user_config(self, project, service_type, config_vars):
        """Convert a list of ["foo.bar='baz'"] to {"foo": {"bar": "baz"}}"""
        if not config_vars:
            return {}

        service_types = self.client.get_service_types(project=project)
        try:
            service_def = service_types[service_type]
        except KeyError:
            raise argx.UserError("Unknown service type {!r}, available options: {}".format(
                service_type, ", ".join(service_types)))

        options = self.collect_user_config_options(service_def["user_config_schema"])
        user_config = {}
        for key_value in self.args.user_config:
            try:
                key, value = key_value.split("=", 1)
            except ValueError:
                raise argx.UserError("Invalid config value: {!r}, expected '<KEY>[.<SUBKEY>]=<JSON_VALUE>'"
                                     .format(key_value))

            opt_schema = options.get(key)
            if not opt_schema:
                # Exact key not found, try generic one
                generic_key = ".".join(key.split(".")[:-1] + ["KEY"])
                opt_schema = options.get(generic_key)

            if not opt_schema:
                raise argx.UserError("Unsupported option {!r}, available options: {}"
                                     .format(key, ", ".join(options) or "none"))

            try:
                value = convert_str_to_value(opt_schema, value)
            except ValueError as ex:
                raise argx.UserError("Invalid value {!r}: {}".format(key_value, ex))

            conf = user_config
            parts = key.split(".")
            for part in parts[:-1]:
                conf.setdefault(part, {})
                conf = conf[part]

            conf[parts[-1]] = value

        return user_config

    @arg.project
    @arg("name", help="Service name")
    @arg("--group-name", help="service group", default="default")
    @arg("-t", "--service-type", help="type of service (see 'service types')", required=True)
    @arg("-p", "--plan", help="subscription plan of service", required=False)
    @arg.cloud
    @arg("--no-fail-if-exists", action="store_true", default=False,
         help="do not fail if service already exists")
    @arg.user_config
    def service_create(self):
        """Create a service"""
        service_type_info = self.args.service_type.split(":")
        service_type = service_type_info[0]

        plan = None
        if len(service_type_info) == 2:
            plan = service_type_info[1]
        elif self.args.plan:
            plan = self.args.plan
        if not plan:
            raise argx.UserError("No subscription plan given")

        project = self.get_project()
        try:
            self.client.create_service(
                project=project,
                service=self.args.name,
                service_type=service_type,
                plan=plan,
                cloud=self.args.cloud,
                group_name=self.args.group_name,
                user_config=self.create_user_config(project, self.args.service_type, self.args.user_config))
        except client.Error as ex:
            print(ex.response)
            if not self.args.no_fail_if_exists or ex.response.status_code != 409:
                raise

            self.log.info("service '%s/%s' already exists", project, self.args.name)

    def _get_powered(self):
        if self.args.power_on and self.args.power_off:
            raise argx.UserError("Only one of --power-on or --power-off can be specified")
        elif self.args.power_on:
            return True
        elif self.args.power_off:
            return False
        else:
            return None

    @arg.project
    @arg("name", help="Service name")
    @arg("--group-name", help="New service group")
    @arg.cloud
    @arg.user_config
    @arg("-p", "--plan", help="subscription plan of service", required=False)
    @arg("--power-on", action="store_true", default=False, help="Power-on the service")
    @arg("--power-off", action="store_true", default=False, help="Temporarily power-off the service")
    def service_update(self):
        """Update service settings"""
        powered = self._get_powered()
        project = self.get_project()
        service = self.client.get_service(project=project, service_name=self.args.name)
        plan = self.args.plan or service["plan"]
        user_config = self.create_user_config(project, service["service_type"], self.args.user_config)
        try:
            self.client.update_service(
                cloud=self.args.cloud,
                group_name=self.args.group_name,
                plan=plan,
                powered=powered,
                project=project,
                service=self.args.name,
                user_config=user_config,
            )
        except client.Error as ex:
            print(ex.response.text)
            raise argx.UserError("Service '{}/{}' update failed".format(project, self.args.name))

    @arg("name", help="Project name")
    @arg.cloud
    def project_switch(self):
        """Switch the default project"""
        projects = self.client.get_projects()
        project_names = [p["project_name"] for p in projects]
        if self.args.name in project_names:
            self.config["default_project"] = self.args.name
            self.config.save()
            self.log.info("Set project %r as the default project", self.args.name)
        else:
            raise argx.UserError("Project {!r} does not exist, available projects: {}".format(
                self.args.name, ", ".join(project_names)))

    @classmethod
    def _project_credit_card(cls, project):
        payment_info = project.get("payment_info")
        if payment_info:
            return "{}/{}".format(project["payment_info"]["user_email"], project["payment_info"]["card_id"])
        else:
            return "N/A"

    @arg("name", help="Project name")
    @arg.card_id
    @arg.cloud
    @arg("--no-fail-if-exists", action="store_true", default=False,
         help="Do not fail if project already exists")
    def project_create(self):
        """Create a project"""
        try:
            project = self.client.create_project(project=self.args.name,
                                                 card_id=self.args.card_id,
                                                 cloud=self.args.cloud)
        except client.Error as ex:
            if not self.args.no_fail_if_exists or ex.response.status_code != 409:
                raise

            self.log.info("Project '%s' already exists", self.args.name)

        self.config["default_project"] = self.args.name
        self.config.save()
        self.log.info("Created project %r (default cloud: %r, credit_card: %r) and set it as the default project",
                      self.args.name, project["default_cloud"], self._project_credit_card(project))

    @arg.json
    @arg.project
    def project_details(self):
        """Show project details"""
        project_name = self.get_project()
        project = self.client.get_project(project=project_name)
        project["credit_card"] = self._project_credit_card(project)
        self.print_response([project],
                            json=self.args.json,
                            table_layout=["project_name", "default_cloud", "credit_card"])

    @arg.json
    def project_list(self):
        """List projects"""
        projects = self.client.get_projects()
        for project in projects:
            project["credit_card"] = self._project_credit_card(project)
        self.print_response(projects,
                            json=self.args.json,
                            table_layout=["project_name", "default_cloud", "credit_card"])

    @arg.project
    @arg("--card-id", help="Card ID")
    @arg.cloud
    def project_update(self):
        """Update a project"""
        project_name = self.get_project()
        try:
            project = self.client.update_project(project=project_name,
                                                 card_id=self.args.card_id,
                                                 cloud=self.args.cloud)
        except client.Error as ex:
            print(ex.response.text)
            raise argx.UserError("Project '{}' update failed".format(project_name))
        self.log.info("Updated project %r, default cloud: %r, credit card: %r",
                      project_name,
                      project["default_cloud"],
                      self._project_credit_card(project))

    @arg.project
    @arg.email
    def project_user_invite(self):
        """Invite a new user to the project"""
        project_name = self.get_project()
        try:
            self.client.invite_project_user(project=project_name, user_email=self.args.email)
        except client.Error as ex:
            print(ex.response.text)
            raise argx.UserError("Project '{}' invite for {} failed".format(project_name, self.args.email))
        self.log.info("Invited %r into project %r", self.args.email, project_name)

    @arg.project
    @arg.email
    def project_user_remove(self):
        """Remove a user from the project"""
        project_name = self.get_project()
        try:
            self.client.remove_project_user(project=project_name, user_email=self.args.email)
        except client.Error as ex:
            print(ex.response.text)
            raise argx.UserError("Project '{}' removal of user {} failed".format(project_name, self.args.email))
        self.log.info("Removed %r from project %r", self.args.email, project_name)

    @arg.json
    @arg.project
    def project_user_list(self):
        """Project user list"""
        project_name = self.get_project()
        try:
            user_list = self.client.list_project_users(project=project_name)
            layout = [["user_email", "member_type", "create_time"]]
            self.print_response(user_list, json=self.args.json, table_layout=layout)
        except client.Error as ex:
            print(ex.response.text)
            raise argx.UserError("Project user listing for '{}' failed".format(project_name))

    @arg.email
    @arg("--real-name", help="User real name", required=True)
    def user_create(self):
        """Create a user"""
        password = self.enter_password("New aiven.io password for {}: ".format(self.args.email),
                                       var="AIVEN_NEW_PASSWORD", confirm=True)
        result = self.client.create_user(email=self.args.email,
                                         password=password,
                                         real_name=self.args.real_name)

        self._write_auth_token_file(token=result["token"], email=self.args.email)

    def _write_auth_token_file(self, token, email):
        with self._open_auth_token_file(mode="w") as fp:
            fp.write(jsonlib.dumps({"auth_token": token, "user_email": email}))
            aiven_credentials_filename = fp.name
        os.chmod(aiven_credentials_filename, 0o600)
        self.log.info("Aiven credentials written to: %s", aiven_credentials_filename)

    def _open_auth_token_file(self, mode="r"):
        default_token_file_path = os.path.join(envdefault.AIVEN_CONFIG_DIR, "aiven-credentials.json")
        auth_token_file_path = (os.environ.get("AIVEN_CREDENTIALS_FILE")
                                or default_token_file_path)
        try:
            return open(auth_token_file_path, mode)
        except IOError as ex:
            if ex.errno == errno.ENOENT and mode == "w":
                aiven_dir = os.path.dirname(auth_token_file_path)
                os.makedirs(aiven_dir)
                os.chmod(aiven_dir, 0o700)
                return open(auth_token_file_path, mode)
            raise

    def _get_auth_token(self):
        token = self.args.auth_token
        if token:
            return token

        try:
            with self._open_auth_token_file() as fp:
                return jsonlib.load(fp)["auth_token"]
        except IOError as ex:
            if ex.errno == errno.ENOENT:
                return None
            raise

    def pre_run(self, func):
        self.client = client.AivenClient(base_url=self.args.url,
                                         show_http=self.args.show_http)
        # Always set CA if we have anything set at the command line or in the env
        if self.args.auth_ca is not None:
            self.client.set_ca(self.args.auth_ca)
        if func == self.user_create:
            # "user create" doesn't use authentication (yet)
            return

        # "user login" does not use client token everything else does
        if func != self.user_login:
            auth_token = self._get_auth_token()
            if auth_token:
                self.client.set_auth_token(auth_token)
            else:
                raise argx.UserError("auth_token is required for all commands")

    @arg.json
    @arg.verbose
    def card_list(self):
        """List credit cards"""
        layout = [["card_id", "name", "country", "exp_year", "exp_month", "last4"]]
        if self.args.verbose:
            layout.append("address_*")
        self.print_response(self.client.get_cards(), json=self.args.json, table_layout=layout)

    def _card_get_stripe_token(self,
                               stripe_publishable_key,
                               name,
                               number,
                               exp_month,
                               exp_year,
                               cvc):
        data = {
            "card[name]": name,
            "card[number]": number,
            "card[exp_month]": exp_month,
            "card[exp_year]": exp_year,
            "card[cvc]": cvc,
            "key": stripe_publishable_key,
        }
        response = requests.post("https://api.stripe.com/v1/tokens", data=data)
        return response.json()["id"]

    @arg.json
    @arg("--cvc", help="Credit card security code", type=int, required=True)
    @arg("--exp-month", help="Card expiration month (1-12)", type=int, required=True)
    @arg("--exp-year", help="Card expiration year", type=int, required=True)
    @arg("--name", help="Name on card", required=True)
    @arg("--number", help="Credit card number", type=int, required=True)
    @arg("--update-project", help="Assign card to project")
    def card_add(self):
        """Add a credit card"""
        stripe_key = self.client.get_stripe_key()["stripe_key"]
        stripe_token = self._card_get_stripe_token(
            stripe_key,
            self.args.name,
            self.args.number,
            self.args.exp_month,
            self.args.exp_year,
            self.args.cvc,
        )
        card = self.client.add_card(stripe_token)
        if self.args.json:
            self.print_response(card, json=True)

        if self.args.update_project:
            self.client.update_project(
                project=self.args.update_project,
                card_id=card["card_id"],
            )

    @arg.json
    @arg("card-id", help="Card ID")
    @arg("--address-city", help="Address city")
    @arg("--address-country", help="Address country")
    @arg("--address-line1", help="Address line #1")
    @arg("--address-line2", help="Address line #2")
    @arg("--address-state", help="Address state")
    @arg("--address-zip", help="Address zip code")
    @arg("--exp-month", help="Card expiration month (1-12)", type=int)
    @arg("--exp-year", help="Card expiration year", type=int)
    @arg("--name", help="Name on card")
    def card_update(self):
        """Update credit card information"""
        card = self.client.update_card(
            address_city=self.args.address_city,
            address_country=self.args.address_country,
            address_line1=self.args.address_line1,
            address_line2=self.args.address_line2,
            address_state=self.args.address_state,
            address_zip=self.args.address_zip,
            card_id=self.args.card_id,
            exp_month=self.args.exp_month,
            exp_year=self.args.exp_year,
            name=self.args.name,
        )
        if self.args.json:
            self.print_response(card, json=True)

    @arg.json
    @arg("card-id", help="Card ID")
    def card_remove(self):
        """Remove a credit card"""
        result = self.client.remove_card(card_id=self.args.card_id)
        if self.args.json:
            self.print_response(result, json=True)

    @arg.json
    def credits_list(self):
        """List claimed credits"""
        layout = [["code", "remaining_value"]]
        self.print_response(self.client.list_credits(), json=self.args.json, table_layout=layout)

    @arg.json
    @arg("code", help="Credit code")
    def credits_claim(self):
        """Claim a credit code"""
        result = self.client.claim_credit(self.args.code)
        if self.args.json:
            self.print_response(result, json=True)


if __name__ == "__main__":
    AivenCLI().main()
