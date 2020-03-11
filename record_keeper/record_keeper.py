#! /usr/bin/env python3

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from . import utils as c_f
from .db_utils import DBManager
import collections
from cycler import cycler
import numpy as np
import glob
import os


class RecordKeeper:
    def __init__(self, tensorboard_writer=None, pickler_and_csver=None, attributes_to_search_for=None):
        self.tensorboard_writer = tensorboard_writer
        self.pickler_and_csver = pickler_and_csver
        self.attributes_to_search_for = [] if attributes_to_search_for is None else attributes_to_search_for

    def append_data(self, group_name, series_name, value, iteration):
        if self.tensorboard_writer is not None:
            tag_name = '%s/%s' % (group_name, series_name)
            if not c_f.is_list_and_has_more_than_one_element(value):
                self.tensorboard_writer.add_scalar(tag_name, value, iteration)
        if self.pickler_and_csver is not None:
            self.pickler_and_csver.append(group_name, series_name, value)

    def update_records(self, record_these, global_iteration, custom_attr_func=None, input_group_name_for_non_objects=None, recursive_types=None):
        for name_in_dict, input_obj in record_these.items():
            if input_group_name_for_non_objects is not None:
                group_name = input_group_name_for_non_objects
                self.append_data(group_name, name_in_dict, input_obj, global_iteration)
            else:
                the_obj = c_f.try_getting_dataparallel_module(input_obj)
                attr_list = self.get_attr_list_for_record_keeper(the_obj)
                name = self.get_record_name(name_in_dict, the_obj) 
                for k in attr_list:
                    v = getattr(the_obj, k)                    
                    self.append_data(name, k, v, global_iteration)
                if custom_attr_func is not None:
                    for k, v in custom_attr_func(the_obj).items():
                        self.append_data(name, k, v, global_iteration)
                if recursive_types is not None:
                    try:
                        for attr_name, attr in vars(input_obj).items():
                            next_record_these = None
                            if isinstance(attr, dict):
                                next_record_these = {"%s_%s"%(name, k): v for k, v in attr.items()}
                            elif any(isinstance(attr, rt) for rt in recursive_types):
                                next_record_these = {"%s_%s"%(name, attr_name): attr}
                            if next_record_these:
                                self.update_records(next_record_these, global_iteration, custom_attr_func, input_group_name_for_non_objects, recursive_types)
                    except:
                        pass


    def get_attr_list_for_record_keeper(self, input_obj):
        attr_list = []
        for k in self.attributes_to_search_for:
            if (hasattr(input_obj, k)) and (getattr(input_obj, k) is not None):
                attr_list += getattr(input_obj, k)
        return attr_list

    def get_record_name(self, name_in_dict, input_obj, key_name=None):
        record_name = "%s_%s" % (name_in_dict, type(input_obj).__name__)
        if key_name:
            record_name += '_%s' % key_name
        return record_name

    def maybe_add_custom_figures_to_tensorboard(self, global_iteration):
        if self.pickler_and_csver is not None:
            for group_name, dict_of_lists in self.pickler_and_csver.records.items():
                for series_name, v in dict_of_lists.items():
                    if len(v) > 0 and isinstance(v[0], list):
                        tag_name = '%s/%s' % (group_name, series_name)
                        figure = self.multi_line_plot(v)
                        self.tensorboard_writer.add_figure(tag_name, figure, global_iteration)

    def multi_line_plot(self, list_of_lists):
        # Each sublist represents a snapshot at an iteration.
        # Transpose so that each row covers many iterations.
        numpified = np.transpose(np.array(list_of_lists))
        fig = plt.figure()
        for sublist in numpified:
            plt.plot(np.arange(numpified.shape[1]), sublist)
        return fig

    def add_embedding_plot(self, embeddings, labels, tag, global_iteration):
        # The pytorch tensorboard function "add_embedding" doesn't seem to work
        # So this will have to do for now
        label_set = np.unique(labels)
        num_classes = len(label_set)
        fig = plt.figure()
        plt.gca().set_prop_cycle(cycler("color", [plt.cm.nipy_spectral(i) for i in np.linspace(0, 0.9, num_classes)]))
        for i in range(num_classes):
            idx = labels == label_set[i]
            plt.plot(embeddings[idx, 0], embeddings[idx, 1], ".", markersize=1)
        self.tensorboard_writer.add_figure(tag, fig, global_iteration)

    def get_record(self, group_name):
        return self.pickler_and_csver.records[group_name]


class PicklerAndCSVer:
    def __init__(self, folder, db_path=None, experiment_name=None, is_new_experiment=True):
        self.records = self.get_empty_nested_dict()
        self.records_temp = self.get_empty_nested_dict()
        self.folder = folder
        c_f.makedir_if_not_there(self.folder)
        self.db_path = db_path
        self.experiment_name = experiment_name
        self.db_manager = None
        if self.db_path:
            assert self.experiment_name is not None
            self.db_manager = DBManager(self.db_path)
            if is_new_experiment: 
                self.db_manager.new_experiment(experiment_name)

    def get_empty_nested_dict(self):
        return collections.defaultdict(lambda: collections.defaultdict(list))

    def append(self, group_name, series_name, input_val):
        if c_f.is_list_and_has_more_than_one_element(input_val):
            convert_func = c_f.convert_to_list
        else:
            convert_func = c_f.convert_to_scalar
        for r in [self.records, self.records_temp]:
            r[group_name][series_name].append(convert_func(input_val))

    def save_records(self):
        for k, v in self.records.items():
            base_filename = os.path.join(self.folder, k)
            c_f.save_pkl(v, base_filename+".pkl")
            c_f.write_dict_of_lists_to_csv(v, base_filename+".csv")
        if self.db_manager is not None:
            for k, v in self.records_temp.items():
                self.db_manager.write(self.experiment_name, k, v)
        self.records_temp = self.get_empty_nested_dict()

    def load_records(self, num_records_to_load=None):
        for filename in list(glob.glob(os.path.join(self.folder,"*.pkl"))):
            k = os.path.splitext(filename.split('/')[-1])[0]
            self.records[k] = c_f.load_pkl(filename)
            if num_records_to_load is not None:
                for zzz, _ in self.records[k].items():
                    self.records[k][zzz] = self.records[k][zzz][:num_records_to_load]
