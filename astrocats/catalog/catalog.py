"""Overarching catalog object for all open catalogs.
"""
import codecs
import importlib
import json
import os
import subprocess
import sys
import warnings
from collections import OrderedDict
from glob import glob

import psutil
from astrocats import __version__
from astrocats.catalog.entry import ENTRY, Entry
from astrocats.catalog.source import SOURCE
from astrocats.catalog.task import Task
from astrocats.catalog.utils import (compress_gz, is_integer, pbar,
                                     read_json_dict, repo_priority,
                                     uncompress_gz, uniq_cdl)
from git import Repo
from tqdm import tqdm


class Catalog:
    """Object to hold the main catalog dictionary and other catalog globals.

    Attributes
    ----------
    OSC_BIBCODE
    OSC_NAME
    OSC_URL
    ADS_BIB_URL
    TRAVIS_QUERY_LIMIT
    COMPRESS_ABOVE_FILESIZE

    """

    OSC_BIBCODE = '2016arXiv160501054G'
    OSC_NAME = 'The Open Supernova Catalog'
    OSC_URL = 'https://sne.space'

    ADS_BIB_URL = ("http://adsabs.harvard.edu/cgi-bin/nph-abs_connect?"
                   "db_key=ALL&version=1&bibcode=")

    TRAVIS_QUERY_LIMIT = 10
    COMPRESS_ABOVE_FILESIZE = 90e6   # bytes

    class PATHS:
        """Store and control catalog file-structure information.

        Individual catalogs must provide the below file structure.
        -   `repos.json`
        -   `tasks.json`

        Attributes
        ----------
        catalog : `astrocats.catalog.catalog.Catalog` (sub)class object
        catalog_dir : str
        tasks_dir : str
        PATH_BASE : str
        PATH_INPUT : str
        PATH_OUTPUT : str
        REPOS_LIST : str
        TASK_LIST : str
        repos_dict : dict
            Dictionary of 'repo-types: repo-lists' key-value pairs.
            Loaded from `REPOS_LIST` file.

        Methods
        -------
        get_all_repo_folders : get a list of paths for all data repositories
        get_repo_boneyard : get the path of the boneyard repository
        get_repo_input_folders : get the paths of all input data repositories
        get_repo_output_file_list : get the paths of all files in output repos
        get_repo_output_folders : get the paths of all input data repositories

        """

        def __init__(self, catalog):
            self.catalog = catalog
            this_file = sys.modules[self.__module__].__file__
            self.catalog_dir = os.path.dirname(this_file)
            self.tasks_dir = os.path.join(self.catalog_dir, 'tasks')
            self.PATH_BASE = os.path.join(
                catalog.args.base_path, self.catalog_dir, '')
            self.PATH_INPUT = os.path.join(self.PATH_BASE, 'input', '')
            self.PATH_OUTPUT = os.path.join(self.PATH_BASE, 'output', '')
            # critical datafiles
            self.REPOS_LIST = os.path.join(self.PATH_INPUT, 'repos.json')
            self.TASK_LIST = os.path.join(self.PATH_INPUT, 'tasks.json')
            self.repos_dict = read_json_dict(self.REPOS_LIST)
            return

        def _get_repo_file_list(self, repo_folders, normal=True, bones=True):
            """Get filenames for files in each repository, `boneyard` optional.
            """
            # repo_folders = get_repo_output_folders()
            files = []
            for rep in repo_folders:
                if 'boneyard' not in rep and not normal:
                    continue
                if not bones and 'boneyard' in rep:
                    continue
                these_files = glob(rep + "/*.json") + glob(rep + "/*.json.gz")
                self.catalog.log.debug("Found {} files in '{}'".format(
                    len(these_files), rep))
                files += these_files

            return files

        def get_all_repo_folders(self, boneyard=True):
            """Get the full paths of all data repositories.
            """
            all_repos = self.get_repo_input_folders()
            all_repos.extend(self.get_repo_output_folders(bones=boneyard))
            return all_repos

        def get_repo_boneyard(self):
            bone_path = self.repos_dict['boneyard']
            try:
                bone_path = bone_path[0]
            except TypeError:
                pass
            bone_path = os.path.join(self.PATH_OUTPUT, bone_path, '')
            return bone_path

        def get_repo_input_folders(self):
            """Get the full paths of the input data repositories.
            """
            repo_folders = []
            repo_folders += self.repos_dict['external']
            repo_folders += self.repos_dict['internal']
            repo_folders = list(sorted(set(repo_folders)))
            repo_folders = [os.path.join(self.PATH_INPUT, rf)
                            for rf in repo_folders]
            return repo_folders

        def get_repo_output_file_list(self, normal=True, bones=True):
            """Get a list of all existing output files.

            These are the files deleted in the `delete_old_entry_files` task.
            """
            repo_folders = self.get_repo_output_folders()
            return self._get_repo_file_list(
                repo_folders, normal=normal, bones=bones)

        def get_repo_output_folders(self, bones=True):
            """Get the full paths of the output data repositories.
            """
            repo_folders = []
            repo_folders += self.repos_dict['output']
            if bones:
                repo_folders += self.repos_dict['boneyard']
            repo_folders = list(sorted(list(set(repo_folders)),
                                       key=lambda key: repo_priority(key)))
            repo_folders = [os.path.join(self.PATH_OUTPUT, rf)
                            for rf in repo_folders]
            return repo_folders

    class SCHEMA:
        HASH = ''
        URL = ''

    def __init__(self, args, log):
        # Store runtime arguments
        self.args = args
        self.log = log
        self.proto = Entry

        # Instantiate PATHS
        self.PATHS = self.PATHS(self)

        # Load repos dictionary (required)
        self.repos_dict = read_json_dict(self.PATHS.REPOS_LIST)
        self.clone_repos()

        # Create empty `entries` collection
        self.entries = OrderedDict()
        self.aliases = {}

        # Only journal tasks with priorities greater than this number,
        # unless updating.
        self.min_journal_priority = 0

        # Store version information
        # -------------------------
        # git `SHA` of this directory (i.e. a sub-catalog)
        my_path = self.PATHS.catalog_dir
        catalog_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=my_path)
        catalog_sha = catalog_sha.decode('ascii').strip()
        # Git SHA of `astrocats`
        parent_path = os.path.join(my_path, os.pardir)
        astrocats_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=parent_path)
        astrocats_sha = astrocats_sha.decode('ascii').strip()
        # Name of this class (if subclassed)
        my_name = type(self).__name__
        self._version_long = "Astrocats v'{}' SHA'{}' - {} SHA'{}".format(
            __version__, astrocats_sha, my_name, catalog_sha)

        return

    def import_data(self):
        """Run all of the import tasks.

        This is executed by the 'scripts.main.py' when the module is run as an
        executable. This can also be run as a method, in which case default
        arguments are loaded, but can be overriden using `**kwargs`.
        """

        tasks_list = self.load_task_list()
        warnings.filterwarnings(
            'ignore', r'Warning: converting a masked element to nan.')
        # FIX
        warnings.filterwarnings(
            'ignore', category=DeprecationWarning)

        # Delete all old (previously constructored) output files
        if self.args.delete_old:
            self.log.warning("Deleting all old entry files.")
            self.delete_old_entry_files()

        if self.args.travis:
            self.log.warning("Running in `travis` mode.")

        prev_priority = 0
        prev_task_name = ''
        # for task, task_obj in tasks_list.items():
        for task_name, task_obj in tasks_list.items():
            if not task_obj.active:
                continue
            self.log.warning("Task: '{}'".format(task_name))

            nice_name = task_obj.nice_name
            mod_name = task_obj.module
            func_name = task_obj.function
            priority = task_obj.priority

            # Make sure things are running in the correct order
            if priority < prev_priority and priority > 0:
                raise RuntimeError(
                    "Priority for '{}': '{}', less than prev,"
                    "'{}': '{}'.\n{}"
                    .format(task_name, priority, prev_task_name, prev_priority,
                            task_obj))

            self.log.debug("\t{}, {}, {}, {}".format(
                nice_name, priority, mod_name, func_name))
            mod = importlib.import_module('.' + mod_name, package='astrocats')
            self.current_task = task_obj
            getattr(mod, func_name)(self)

            num_events, num_stubs = self.count()
            self.log.warning("Task finished.  Events: {},  Stubs: {}".format(
                num_events, num_stubs))
            self.journal_entries()
            num_events, num_stubs = self.count()
            self.log.warning("Journal finished.  Events: {}, Stubs: {}".format(
                num_events, num_stubs))

            prev_priority = priority
            prev_task_name = task_name

        process = psutil.Process(os.getpid())
        memory = process.memory_info().rss
        self.log.warning('Memory used (MBs): '
                         '{:,}'.format(memory / 1024. / 1024.))
        return

    def load_task_list(self):
        """Load the list of tasks in this catalog's 'input/tasks.json' file.

        A `Task` object is created for each entry, with the parameters filled
        in. These are placed in an OrderedDict, sorted by the `priority`
        parameter, with positive values and then negative values,
            e.g. [0, 2, 10, -10, -1].
        """

        # Dont allow both a 'min' and 'max' task priority
        if ((self.args.min_task_priority is not None and
             self.args.max_task_priority is not None)):
            raise ValueError("Can only use *either* 'min' *or* 'max' priority")

        # Load tasks data from input json file
        tasks, task_names = self._load_task_list_from_file()

        # Make sure 'active' modification lists are all valid
        args_lists = [self.args.args_task_list,
                      self.args.yes_task_list, self.args.no_task_list]
        args_names = ['--tasks', '--yes', '--no']
        for arglist, lname in zip(args_lists, args_names):
            if arglist is not None:
                for tname in arglist:
                    if tname not in task_names:
                        raise ValueError(
                            "Value '{}' in '{}' list does not match"
                            " any tasks".format(tname, lname))

        # Process min/max priority specification ('None' if none given)
        min_priority = _get_task_priority(tasks, self.args.min_task_priority)
        max_priority = _get_task_priority(tasks, self.args.max_task_priority)
        task_groups = self.args.task_groups
        if task_groups is not None:
            if not isinstance(task_groups, list):
                task_groups = [task_groups]

        # Iterate over all tasks to determine which should be (in)active
        # --------------------------------------------------------------
        for key in tasks:
            # If specific list of tasks is given, make only those active
            if self.args.args_task_list is not None:
                if key in self.args.args_task_list:
                    tasks[key].active = True
                else:
                    tasks[key].active = False

            # Only run tasks above minimum priority
            # (doesn't modify negtive priority tasks)
            if min_priority is not None and tasks[key].priority >= 0:
                tasks[key].active = False
                if tasks[key].priority >= min_priority:
                    tasks[key].active = True

            # Only run tasks below maximum priority
            # (doesnt modify negative priority tasks)
            if max_priority is not None and tasks[key].priority >= 0:
                tasks[key].active = False
                if tasks[key].priority <= max_priority:
                    tasks[key].active = True

            # Set 'yes' tasks to *active*
            if self.args.yes_task_list is not None:
                if key in self.args.yes_task_list:
                    tasks[key].active = True
            # Set 'no' tasks to *inactive*
            if self.args.no_task_list is not None:
                if key in self.args.no_task_list:
                    tasks[key].active = False
            # Set tasks in target 'groups' to *active*
            if task_groups is not None and tasks[key].groups is not None:
                # Go through each group defined in the command line
                for given_group in task_groups:
                    # If this task is a member of any of those groups
                    if given_group in tasks[key].groups:
                        tasks[key].active = True
                        break

        # Sort entries as positive values, then negative values
        #    [0, 1, 2, 2, 10, -100, -10, -1]
        # Tuples are sorted by first element (here: '0' if positive), then
        # second (here normal order)
        tasks = OrderedDict(sorted(tasks.items(), key=lambda t: (
            t[1].priority < 0, t[1].priority, t[1].name)))

        # Find the first task that has "always_journal" set to True
        for key in tasks:
            if tasks[key].active and tasks[key].always_journal:
                self.min_journal_priority = tasks[key].priority
                break

        names_act = []
        names_inact = []
        for key, val in tasks.items():
            if val.active:
                names_act.append(key)
            else:
                names_inact.append(key)

        self.log.info("Active Tasks:\n\t" + ", ".join(nn for nn in names_act))
        self.log.debug("Inactive Tasks:\n\t" +
                       ", ".join(nn for nn in names_inact))
        return tasks

    def _load_task_list_from_file(self):
        """
        """
        def_task_list_filename = self.PATHS.TASK_LIST
        self.log.debug(
            "Loading task-list from '{}'".format(def_task_list_filename))
        data = json.load(open(def_task_list_filename, 'r'))
        # Create `Task` objects for each element in the tasks data file
        tasks = {}
        task_names = []
        for key, val in data.items():
            tasks[key] = Task(name=key, **val)
            task_names.append(key)
        return tasks, task_names

    def save_caches(self):
        return

    def _clone_repos(self, all_repos):
        """Given a list of repositories, make sure they're all cloned.

        Should be called from the subclassed `Catalog` objects, passed a list
        of specific repository names.

        Arguments
        ---------
        all_repos : list of str
            *Absolute* path specification of each target repository.

        """
        for repo in all_repos:
            if not os.path.isdir(repo):
                try:
                    repo_name = os.path.split(repo)[-1]
                    self.log.warning(
                        'Cloning "' + repo + '" (only needs to be done ' +
                        'once, may take few minutes per repo).')
                    Repo.clone_from("https://github.com/astrocatalogs/" +
                                    repo_name + ".git", repo,
                                    **({'depth': self.args.clone_depth} if
                                       self.args.clone_depth > 0 else {}))
                except:
                    self.log.error("CLONING '{}' INTERRUPTED".format(repo))
                    raise

        return

    def clone_repos(self):
        self._clone_repos([])

    def git_add_commit_push_all_repos(self):
        """Add all files in each data repository tree, commit, push.

        Creates a commit message based on the current catalog version info.

        If either the `git add` or `git push` commands fail, an error will be
        raised.  Currently, if `commit` fails an error *WILL NOT* be raised
        because the `commit` command will return a nonzero exit status if
        there are no files to add... which we dont want to raise an error.
        FIX: improve the error checking on this.
        """
        all_repos = self.PATHS.get_all_repo_folders()
        for repo in all_repos:
            self.log.warning("Repo in: '{}'".format(repo))
            # Get the initial git SHA
            git_comm = "git rev-parse HEAD {}".format(repo)
            sha_beg = subprocess.getoutput(git_comm)
            self.log.debug("Current SHA: '{}'".format(sha_beg))

            # Get files that should be added, compress and check sizes
            add_files = self._prep_git_add_file_list(
                repo, self.COMPRESS_ABOVE_FILESIZE)
            self.log.info("Found {} Files to add.".format(len(add_files)))
            if len(add_files) == 0:
                continue

            try:
                # Add all files in the repository directory tree
                git_comm = ["git", "add"]
                git_comm.extend(add_files)
                _call_command_in_repo(git_comm, repo, self.log,
                                      fail=True, log_flag=False)

                # Commit these files
                commit_msg = "'push' - adding all files."
                commit_msg = "{} : {}".format(self._version_long, commit_msg)
                self.log.info(commit_msg)
                git_comm = ["git", "commit", "-am", commit_msg]
                _call_command_in_repo(git_comm, repo, self.log)

                # Add all files in the repository directory tree
                git_comm = ["git", "push"]
                _call_command_in_repo(git_comm, repo, self.log, fail=True)
            except Exception as err:
                try:
                    git_comm = ["git", "reset", "HEAD"]
                    _call_command_in_repo(git_comm, repo, self.log, fail=True)
                except:
                    pass

                raise err

        return

    def load_entry_from_name(self, name, delete=True, merge=True):
        loaded_entry = self.proto.init_from_file(self, name=name, merge=merge)
        if loaded_entry is not None:
            self.entries[name] = loaded_entry
            self.log.debug(
                "Added '{}', from '{}', to `self.entries`".format(
                    name, loaded_entry.filename))
            # Delete source file, if desired
            if delete:
                self._delete_entry_file(entry=loaded_entry)
            return name
        return None

    def add_entry(self, name, load=True, delete=True):
        """Find an existing entry in, or add a new one to, the `entries` dict.

        FIX: rename to `create_entry`???

        Returns
        -------
        entries : OrderedDict of Entry objects
        newname : str
            Name of matching entry found in `entries`, or new entry added to
            `entries`
        """
        newname = self.clean_entry_name(name)
        # If entry already exists, return
        if newname in self.entries:
            self.log.debug(
                "`newname`: '{}' (name: '{}') already exists.".
                format(newname, name))
            # If this is a stub, we need to continue, possibly load file
            if self.entries[newname]._stub:
                self.log.debug("'{}' is a stub".format(newname))
            # If a full (non-stub) event exists, return its name
            else:
                self.log.debug("'{}' is not a stub, returning".format(newname))
                return newname

        # If entry is alias of another entry in `entries`, find and return that
        match_name = self.find_entry_name_of_alias(newname)
        if match_name is not None:
            self.log.debug(
                "`newname`: '{}' (name: '{}') already exists as alias for "
                "'{}'.".format(newname, name, match_name))
            newname = match_name

        # Load entry from file
        if load:
            loaded_name = self.load_entry_from_name(newname, delete=delete)
            if loaded_name:
                return loaded_name

        # If we match an existing event, return that
        if match_name is not None:
            return match_name

        # Create new entry
        new_entry = self.proto(self, newname)
        new_entry[self.proto._KEYS.SCHEMA] = self.SCHEMA.URL
        self.log.log(self.log._LOADED,
                     "Created new entry for '{}'".format(newname))
        # Add entry to dictionary
        self.entries[newname] = new_entry
        return newname

    def delete_old_entry_files(self):
        if len(self.entries):
            err_str = "`delete_old_entry_files` with `entries` not empty!"
            self.log.error(err_str)
            raise RuntimeError(err_str)
        # Delete all old entry JSON files
        repo_files = self.PATHS.get_repo_output_file_list()
        for rfil in pbar(repo_files, desc='Deleting old entries'):
            os.remove(rfil)
            self.log.debug("Deleted '{}'".format(os.path.split(rfil)[-1]))
        return

    def get_preferred_name(self, name):
        if name not in self.entries:
            # matches = []
            for entry in self.entries:
                aliases = self.entries[entry].get_aliases(includename=False)
                if len(aliases) > 1 and name in aliases:
                    return entry
            return name
        else:
            return name

    def find_entry_name_of_alias(self, alias):
        """Return the first entry name with the given 'alias' included in its
        list of aliases.

        Returns
        -------
        name of matching entry (str) or 'None' if no matches

        """
        if alias in self.aliases:
            name = self.aliases[alias]
            if name in self.entries:
                return name
            else:
                # Name wasn't found, possibly merged or deleted. Now look
                # really hard.
                for name, entry in self.entries.items():
                    aliases = entry.get_aliases(includename=False)
                    if alias in aliases:
                        if ((ENTRY.DISTINCT_FROM not in entry) or
                                (alias not in entry[ENTRY.DISTINCT_FROM])):
                            return name

        return None

    def copy_to_entry_in_catalog(self, fromname, destname):
        self.copy_entry_to_entry(self.entries[fromname],
                                 self.entries[destname])

    def copy_entry_to_entry(self, fromentry, destentry):
        """

        Used by `merge_duplicates`
        """
        self.log.info("Copy entry object '{}' to '{}'"
                      .format(fromentry[fromentry._KEYS.NAME],
                              destentry[destentry._KEYS.NAME]))
        newsourcealiases = {}

        if self.proto._KEYS.SOURCES in fromentry:
            for source in fromentry[self.proto._KEYS.SOURCES]:
                alias = source.pop(SOURCE.ALIAS)
                newsourcealiases[alias] = source

        if self.proto._KEYS.ERRORS in fromentry:
            for err in fromentry[self.proto._KEYS.ERRORS]:
                destentry.setdefault(
                    self.proto._KEYS.ERRORS, []).append(err)

        for key in fromentry:
            if fromentry._KEYS.get_key_by_name(key).no_source:
                continue
            for item in fromentry[key]:
                # isd = False
                if 'source' not in item:
                    raise ValueError("Item has no source!")

                nsid = []
                for sid in item['source'].split(','):
                    if sid in newsourcealiases:
                        source = newsourcealiases[sid]
                        nsid.append(destentry
                                    .add_source(**source))
                    else:
                        raise ValueError("Couldn't find source alias!")

                item['source'] = uniq_cdl(nsid)

                if key == ENTRY.PHOTOMETRY:
                    destentry.add_photometry(**item)
                elif key == ENTRY.SPECTRA:
                    destentry.add_spectrum(**item)
                elif key == ENTRY.ERRORS:
                    destentry.add_error(**item)
                else:
                    destentry.add_quantity(check_for_dupes=False, quantity=key,
                                           **item)

        return

    def clean_entry_name(self, name):
        """Template method to clean/sanitize an entry name before setting it.

        Should be overridden appropriately in subclassed `Catalog` objects.
        """
        return name

    def new_entry(self, name, load=True, delete=True,
                  loadifempty=True, srcname='', reference='', url='',
                  bibcode='', secondary=False, acknowledgment=''):
        newname = self.add_entry(name, load=load, delete=delete)
        source = self.entries[newname].add_source(
            bibcode=bibcode, name=srcname, reference=reference, url=url,
            secondary=secondary, acknowledgment=acknowledgment)
        self.entries[newname].add_quantity(ENTRY.ALIAS, name, source)
        return newname, source

    def merge_duplicates(self):
        """Merge and remove duplicate entries.

        Compares each entry ('name') in `stubs` to all later entries to check
        for duplicates in name or alias.  If a duplicate is found, they are
        merged and written to file.
        """
        if len(self.entries) == 0:
            self.log.error("WARNING: `entries` is empty, loading stubs")
            if self.args.update:
                self.log.warning(
                    "No sources changed, entry files unchanged in update."
                    "  Skipping merge.")
                return
            self.entries = self.load_stubs()

        task_str = self.get_current_task_str()

        keys = list(sorted(self.entries.keys()))
        n1 = 0
        mainpbar = tqdm(total=len(keys), desc=task_str)
        while n1 < len(keys):
            name1 = keys[n1]
            if name1 not in self.entries:
                self.log.info("Entry for {} not found, likely already "
                              "deleted in merging process.".format(name1))
                n1 = n1 + 1
                mainpbar.update(1)
                continue
            allnames1 = set(self.entries[name1].get_aliases() +
                            self.entries[name1].extra_aliases())

            # Search all later names
            for name2 in keys[n1 + 1:]:
                if name1 == name2:
                    continue
                if name1 not in self.entries:
                    self.log.info("Entry for {} not found, likely already "
                                  "deleted in merging process.".format(name1))
                    continue
                if name2 not in self.entries:
                    self.log.info("Entry for {} not found, likely already "
                                  "deleted in merging process.".format(name2))
                    continue

                allnames2 = set(self.entries[name2].get_aliases() +
                                self.entries[name2].extra_aliases())

                # If there are any common names or aliases, merge
                if len(allnames1 & allnames2):
                    self.log.warning(
                        "Found two entries with common aliases "
                        "('{}' and '{}'), merging.".format(name1, name2))

                    load1 = self.proto.init_from_file(
                        self, name=name1)
                    load2 = self.proto.init_from_file(
                        self, name=name2)
                    if load1 is not None and load2 is not None:
                        # Delete old files
                        self._delete_entry_file(entry=load1)
                        self._delete_entry_file(entry=load2)
                        self.entries[name1] = load1
                        self.entries[name2] = load2
                        priority1 = 0
                        priority2 = 0
                        for an in allnames1:
                            if an.startswith(self.entries[name1]
                                             .priority_prefixes()):
                                priority1 += 1
                        for an in allnames2:
                            if an.startswith(self.entries[name2]
                                             .priority_prefixes()):
                                priority2 += 1

                        if priority1 > priority2:
                            self.copy_to_entry_in_catalog(name2, name1)
                            keys.append(name1)
                            del self.entries[name2]
                        else:
                            self.copy_to_entry_in_catalog(name1, name2)
                            keys.append(name2)
                            del self.entries[name1]
                    else:
                        self.log.warning('Duplicate already deleted')

                    # if len(self.entries) != 1:
                    #     self.log.error(
                    #         "WARNING: len(entries) = {}, expected 1.  "
                    #         "Still journaling...".format(len(self.entries)))
                    self.journal_entries()

            if self.args.travis and n1 > self.TRAVIS_QUERY_LIMIT:
                break
            n1 = n1 + 1
            mainpbar.update(1)
        mainpbar.close()

    def sanitize(self):
        task_str = self.get_current_task_str()
        for name in pbar(list(sorted(self.entries.keys())), task_str):
            self.add_entry(name)
            self.journal_entries(bury=True, final=True)

    def load_stubs(self):
        """
        """
        currenttask = 'Loading entry stubs'
        files = self.PATHS.get_repo_output_file_list()
        for fi in pbar(files, currenttask):
            fname = fi
            # FIX: should this be ``fi.endswith(``.gz')`` ?
            if '.gz' in fi:
                fname = uncompress_gz(fi)
            name = os.path.basename(
                os.path.splitext(fname)[0]).replace('.json', '')
            new_entry = self.proto.init_from_file(
                self, path=fname, delete=False)
            # Make sure a non-stub entry doesnt already exist with this name
            if name in self.entries and not self.entries[name]._stub:
                err_str = (
                    "ERROR: non-stub entry already exists with name '{}'"
                    .format(name))
                self.log.error(err_str)
                raise RuntimeError(err_str)

            self.entries[name] = new_entry.get_stub()
            self.log.debug("Added stub for '{}'".format(name))

        return self.entries

    def _delete_entry_file(self, entry_name=None, entry=None):
        """Delete the file associated with the given entry.
        """
        if entry_name is None and entry is None:
            raise RuntimeError("Either `entry_name` or `entry` must be given.")
        elif entry_name is not None and entry is not None:
            raise RuntimeError("Cannot use both `entry_name` and `entry`.")

        if entry_name is not None:
            entry = self.entries[entry_name]
        else:
            entry_name = entry[ENTRY.NAME]

        outdir, filename = entry._get_save_path()
        # FIX: do we also need to check for gzipped files??
        entry_filename = os.path.join(outdir, filename + '.json')
        # entry_filename = entry.filename

        if self.args.write_entries:
            self.log.info("Deleting entry file '{}' of entry '{}'".format(
                entry_filename, entry_name))
            if not os.path.exists(entry_filename):
                self.log.error("Filename '{}' does not exist".format(
                    entry_filename))
            os.remove(entry_filename)
        else:
            self.log.debug("Not deleting '{}' because `write_entries`"
                           " is False".format(entry_filename))

        return

    def should_bury(self, name):
        return (False, True)

    def journal_entries(self, clear=True, gz=False, bury=False,
                        write_stubs=False, final=False):
        """Write all entries in `entries` to files, and clear.  Depending on
        arguments and `tasks`.

        Iterates over all elements of `entries`, saving (possibly 'burying')
        and deleting.
        -   If ``clear == True``, then each element of `entries` is deleted,
            and a `stubs` entry is added
        """

        # if (self.current_task.priority >= 0 and
        #        self.current_task.priority < self.min_journal_priority):
        #    return

        # Write it all out!
        # NOTE: this needs to use a `list` wrapper to allow modification of
        # dict
        for name in list(self.entries.keys()):
            if self.args.write_entries:
                # If this is a stub and we aren't writing stubs, skip
                if self.entries[name]._stub and not write_stubs:
                    continue

                # Bury non-SN entries here if only claimed type is non-SN type,
                # or if primary name starts with a non-SN prefix.
                bury_entry = False
                save_entry = True
                if bury:
                    (bury_entry, save_entry) = self.should_bury(name)

                if save_entry:
                    save_name = self.entries[name].save(bury=bury_entry,
                                                        final=final)
                    self.log.info(
                        "Saved {} to '{}'.".format(name.ljust(20), save_name))
                    if (gz and os.path.getsize(save_name) >
                            self.COMPRESS_ABOVE_FILESIZE):
                        save_name = compress_gz(save_name)
                        self.log.debug("Compressed '{}' to '{}'".format(
                            name, save_name))
                        # FIX: use subprocess
                        outdir, filename = os.path.split(save_name)
                        filename = filename.split('.')[0]
                        os.system('cd ' + outdir + '; git rm --cached ' +
                                  filename +
                                  '.json; git add -f ' + filename +
                                  '.json.gz; cd ' + self.PATHS.PATH_BASE)

            if clear:
                self.entries[name] = self.entries[name].get_stub()
                self.log.debug("Entry for '{}' converted to stub".format(name))

        return

    def entry_exists(self, name):
        if name in self.entries:
            return True
        for ev in self.entries:
            if name in self.entries[ev].get_aliases(includename=False):
                return True
        return False

    def count(self):
        full = 0
        stub = 0
        for ev in self.entries:
            if self.entries[ev]._stub:
                stub += 1
            else:
                full += 1
        return full, stub

    def get_current_task_str(self):
        """Get a string describing the current task the catalog is working on.
        """
        return self.current_task.current_task(self.args)

    def get_current_task_repo(self):
        """Get the data repository corresponding to the currently active task.
        """
        return self.current_task._get_repo_path(self.PATHS.PATH_BASE)

    def set_preferred_names(self):
        """Choose between each entries given name and its possible aliases for
        the best one.
        """
        if len(self.entries) == 0:
            self.log.error("WARNING: `entries` is empty, loading stubs")
            self.load_stubs()

        task_str = self.get_current_task_str()
        for ni, oname in enumerate(pbar(self.entries, task_str)):
            name = self.add_entry(oname)
            self.entries[name].set_preferred_name()

            if self.args.travis and ni > self.TRAVIS_QUERY_LIMIT:
                break

        return

    def load_cached_url(self, url, filepath, timeout=120, write=True,
                        failhard=False, jsonsort=''):
        from hashlib import md5
        filemd5 = ''
        file_txt = ''
        # Load existing, cached copy of online data file
        if not self.args.refresh and os.path.isfile(filepath):
            with codecs.open(filepath, 'r', encoding='utf8') as f:
                file_txt = f.read()
                self.log.debug("{}: Loaded `file_txt` from '{}'.".format(
                    self.current_task, filepath))
                if self.args.update:
                    filemd5 = md5(file_txt.encode('utf-8')).hexdigest()

        # Try to download new copy of online data
        try:
            import requests
            session = requests.Session()
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            # Look for errors
            for x in response.history:
                x.raise_for_status()
                if (x.status_code == 500 or x.status_code == 307 or
                        x.status_code == 404):
                    raise
            url_txt = response.text
            self.log.debug("{}: Loaded `url_txt` from '{}'.".format(
                self.current_task, url))
            newmd5 = md5(url_txt.encode('utf-8')).hexdigest()
            # tprint(filemd5 + ": " + newmd5)
            # Check if cached file and newly downloaded file are the same
            # If so: no need to resave it, return
            if self.args.update and newmd5 == filemd5:
                self.log.debug(
                    'Skipping file in "' + self.current_task +
                    '," local and remote copies identical [' + newmd5 + '].')
                return False
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            if failhard:
                return ''
            return file_txt
        else:
            # Write the newly downloaded data to the cache save file.
            if write:
                wtxt = url_txt if url_txt else file_txt
                if jsonsort and '.json' in filepath:
                    jdict = json.loads(wtxt)
                    wtxt = json.dumps(
                        list(sorted(jdict, key=lambda kk: kk[jsonsort])),
                        indent=4, separators=(',', ': '))
                with codecs.open(filepath, 'w', encoding='utf8') as f:
                    f.write(wtxt)
                    self.log.debug("{}: wrote txt to '{}'.".format(
                        self.current_task, filepath))

        return url_txt

    def _prep_git_add_file_list(self, repo, size_limit,
                                fail=True, file_types=None):
        """Get a list of files which should be added to the given repository.

        Notes
        -----
        * Finds files in the *root* of the given repository path.
        * If `file_types` is given, only use those file types.
        * If an uncompressed file is above the `size_limit`, it is compressed.
        * If a compressed file is above the file limit, an error is raised
          (if `fail = True`) or it is skipped (if `fail == False`).

        Arguments
        ---------
        repo : str
            Path to repository
        size_limit : scalar
        fail : bool
            Raise an error if a compressed file is still above the size limit.
        file_types : list of str or None
            Exclusive list of file types to add. 'None' to add all filetypes.

        """
        add_files = []
        if file_types is None:
            file_patterns = ['*']
        else:
            self.log.error(
                "WARNING: uncertain behavior with specified file types!")
            file_patterns = ['*.' + ft for ft in file_types]

        # Construct glob patterns for each file-type
        file_patterns = [os.path.join(repo, fp) for fp in file_patterns]
        for pattern in file_patterns:
            file_list = glob(pattern)
            for ff in file_list:
                fsize = os.path.getsize(ff)
                fname = str(ff)
                comp_failed = False
                # If the found file is too large
                if fsize > size_limit:
                    self.log.debug("File '{}' size '{}' MB.".format(
                        fname, fsize/1028/1028))
                    # If the file is already compressed... fail or skip
                    if ff.endswith('.gz'):
                        self.log.error(
                            "File '{}' is already compressed.".format(fname))
                        comp_failed = True
                    # Not yet compressed - compress it
                    else:
                        fname = compress_gz(fname)
                        fsize = os.path.getsize(fname)
                        self.log.info(
                            "Compressed to '{}', size '{}' MB".format(
                                fname, fsize/1028/1028))
                        # If still too big, fail or skip
                        if fsize > size_limit:
                            comp_failed = True

                # If compressed file is too large, skip file or raise error
                if comp_failed:
                    # Raise an error
                    if fail:
                        raise RuntimeError(
                            "File '{}' cannot be added!".format(fname))
                    # Skip file without adding it
                    self.log.info("Skipping file.")
                    continue

                # If everything is good, add file to list
                add_files.append(fname)

        return add_files


def _get_task_priority(tasks, task_priority):
    """Get the task `priority` corresponding to the given `task_priority`.

    If `task_priority` is an integer or 'None', return it.
    If `task_priority` is a str, return the priority of the task it matches.
    Otherwise, raise `ValueError`.
    """
    if task_priority is None:
        return None
    if is_integer(task_priority):
        return task_priority
    if isinstance(task_priority, str):
        if task_priority in tasks:
            return tasks[task_priority].priority

    raise ValueError("Unrecognized task priority '{}'".format(task_priority))


def _call_command_in_repo(comm, repo, log, fail=False, log_flag=True):
    """Use `subprocess` to call a command in a certain (repo) directory.

    Logs the output (both `stderr` and `stdout`) to the log, and checks the
    return codes to make sure they're valid.  Raises error if not.

    Raises
    ------
    exception `subprocess.CalledProcessError`: if the command fails

    """
    if log_flag:
        log.debug("Running '{}'.".format(" ".join(comm)))
    retval = subprocess.run(comm, cwd=repo, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    if retval.stderr is not None:
        err_msg = retval.stderr.decode('ascii').strip().splitlines()
        for em in err_msg:
            log.error(em)
    if retval.stdout is not None:
        out_msg = retval.stdout.decode('ascii').strip().splitlines()
        for om in out_msg:
            log.info(om)
    # Raises an error if the command failed.
    if fail:
        retval.check_returncode()
    return
