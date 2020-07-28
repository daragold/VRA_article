# -*- coding: utf-8 -*-
"""
Created on Mon Jul 13 13:29:26 2020

@author: darac
"""

import random
import csv
import os
import shutil
from functools import partial
import json
import math
import numpy as np
import geopandas as gpd
import matplotlib
#matplotlib.use('Agg')
import seaborn as sns
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import pandas as pd
from gerrychain import (
    Election,
    Graph,
    MarkovChain,
    Partition,
    accept,
    constraints,
    updaters,
)
from gerrychain.metrics import efficiency_gap, mean_median
from gerrychain.proposals import recom
from gerrychain.updaters import cut_edges
from gerrychain.updaters import *
from gerrychain.tree import recursive_tree_part
from gerrychain.updaters import Tally
from gerrychain import GeographicPartition
from scipy.spatial import ConvexHull
from gerrychain.proposals import recom, propose_random_flip
from gerrychain.tree import recursive_tree_part
from gerrychain.accept import always_accept
from gerrychain.constraints import single_flip_contiguous, Validator
import collections
from enum import Enum
import re
import statsmodels.formula.api as smf
import statsmodels.api as sm
import scipy
from scipy import stats
import intervals as I
import time
import heapq
import operator
from operator import itemgetter

#user inputs
map_num_test = 0
display_dist = 28#0 index
display_elec = '16G_President'
run_name = 'local_test_prob'
tot_pop = 'TOTPOP_x'
num_districts = 36
cand_drop_thresh = 0
elec_weighting = 'district' #or district, statewide
plot_path = 'tx-results-cvap-adjoined/tx-results-cvap-adjoined.shp'  #for shapefile
assign_test = "CD" #map to assess (by column title in shapefile)
#assign_test = "Map{}".format(map_num_test)

#read files
elec_data = pd.read_csv("TX_elections.csv")
elections = list(elec_data["Election"]) #elections we care about

elec_type = elec_data["Type"]
election_returns = pd.read_csv("TX_statewide_election_returns.csv")
dropped_elecs = pd.read_csv("dropped_elecs.csv")["Dropped Elections"]
elec_cand_list = list(election_returns.columns)[2:] #all candidates in all elections
recency_weights = pd.read_csv("recency_weights.csv")
min_cand_weights = pd.read_csv("min_pref_weight_binary.csv")
cand_race_table = pd.read_csv("CandidateRace.csv")

#elections data structures
elecs_bool = ~elec_data.Election.isin(list(dropped_elecs))
elec_data_trunc = elec_data[elecs_bool].reset_index(drop = True)
elec_sets = list(set(elec_data_trunc["Election Set"]))
elections = list(elec_data_trunc["Election"])
general_elecs = list(elec_data_trunc[elec_data_trunc["Type"] == 'General'].Election)
primary_elecs = list(elec_data_trunc[elec_data_trunc["Type"] == 'Primary'].Election)
runoff_elecs = list(elec_data_trunc[elec_data_trunc["Type"] == 'Runoff'].Election)
elec_set_dict = {}
for elec_set in elec_sets:
    elec_set_df = elec_data_trunc[elec_data_trunc["Election Set"] == elec_set]
    elec_set_dict[elec_set] = dict(zip(elec_set_df.Type, elec_set_df.Election))
elec_match_dict = dict(zip(elec_data_trunc["Election"], elec_data_trunc["Election Set"]))
#for effectiveness on different maps:
#stored_plans = pd.read_csv("store_plans_TX_chain_free_NEW.csv")

state_gdf = gpd.read_file(plot_path)
state_gdf["CD"] = [int(i) for i in state_gdf["CD"]]
#state_gdf["Map{}".format(map_num_test)] = state_gdf.index.map(dict(zip(stored_plans["Index"], stored_plans["Map{}".format(map_num_test)])))
#to edit cut off shape file columns
election_return_cols = list(election_returns.columns)
cand1_index = election_return_cols.index('RomneyR_12G_President') #first
cand2_index = election_return_cols.index('ObamaD_12P_President') #last
elec_results_trunc = election_return_cols[cand1_index:cand2_index+1]
state_gdf_cols = list(state_gdf.columns)
cand1_index = state_gdf_cols.index('RomneyR_12')
cand2_index = state_gdf_cols.index('ObamaD_12P')
state_gdf_cols[cand1_index:cand2_index+1] = elec_results_trunc
state_gdf.columns = state_gdf_cols

#make graph from gdf
graph = Graph.from_geodataframe(state_gdf)
graph.add_data(state_gdf)

#reformat elections return - get summary stats
state_df = pd.DataFrame(state_gdf)
state_df = state_df.drop(['geometry'], axis = 1)

#make candidate dictionary (key is election and value is candidates)
candidates = {}
for elec in elections: #only elections we care about
    #get rid of republican candidates in primaries or runoffs (primary runoffs)
    cands = [y for y in elec_cand_list if elec in y and "R_" not in re.sub(elec, '', y) ] if \
    "R_" in elec or "P_" in elec else [y for y in elec_cand_list if elec in y] 
               
    #in general elections, only include 2 major party candidates
    #in all other elections, only include candidates whose vote share is above cand_drop_thresh
    if elec in general_elecs:
        cands = cands[:2]
    if elec not in general_elecs:
       pattern = '|'.join(cands)
       elec_df = state_df.copy().loc[:, state_df.columns.str.contains(pattern)]
       elec_df["Total"] = elec_df.sum(axis=1)
       if elec == '18P_Governor':
           elec_df.to_csv("elec test.csv")
       for cand in cands:
           if sum(elec_df["{}".format(cand)])/sum(elec_df["Total"]) < cand_drop_thresh:
               cands = [i for i in cands if i != cand]   
               print("removed!", cand)

    for cand in cands:
        state_df["{}%CVAP".format(cand)] = state_df["{}".format(cand)]/state_df["CVAP"]    
        
    candidates[elec] = dict(zip(list(range(len(cands))), cands))

#clean data: drop precincts with CVAP = 0 
state_df = state_df[state_df["CVAP"] > 0] #DOUBLE CHECK JUST FOR REGRESSIONS IN CHAIN MODEL!!
state_df["WCVAP%"] = state_df["WCVAP"]/state_df["CVAP"]
state_df["HCVAP%"] = state_df["HCVAP"]/state_df["CVAP"]
state_df["BCVAP%"] = state_df["BCVAP"]/state_df["CVAP"]
            
state_df.to_csv("state_df.csv")

my_updaters = {
        "population": updaters.Tally(tot_pop, alias = "population")
      }

election_functions = [Election(j, candidates[j]) for j in elections]
election_updaters = {election.name: election for election in election_functions}
my_updaters.update(election_updaters)

partition = GeographicPartition(graph = graph, assignment = assign_test, updaters = my_updaters)
num_districts = len(partition)

def winner(partition, election, elec_cands):
    order = [x for x in partition.parts]
    perc_for_cand = {}
    for j in range(len(elec_cands)):
        perc_for_cand[j] = dict(zip(order, partition[election].percents(j)))
    winners = {}
    for i in range(len(partition)):
        dist_percents = [perc_for_cand[z][i] for z in range(len(elec_cands))]
        winner_index = dist_percents.index(max(dist_percents))
        winners[i] = elec_cands[winner_index]
    return winners
    
def f(x, dist_list):
    product = 1
    for i in dist_list.keys():
        mean = dist_list[i][0]
        std = dist_list[i][1]
        dist_from_mean = abs(x-mean)
        ro = scipy.stats.norm.cdf(mean+dist_from_mean, mean, std) - scipy.stats.norm.cdf(mean-dist_from_mean, mean, std)
        product = product*ro
    return product

def norm_dist_params(y, y_pred, sum_params, pop_weights): #y_predict is vector of predicted values, sum_params is prediction when x = 100%
    mean = sum_params #predicted value at x = 100%
    n = len(y)
    y_resid = [len(pop_weights)*w_i*(y_i - y_hat)**2 for w_i,y_i, y_hat in zip(pop_weights,y,y_pred)]
    var = sum(y_resid)/(n-2)   
    std = np.sqrt(var)
    return mean, std


#prepare dataframes for results all for single map
#df 1B and 1H: rows = elecs, columns = districtss, entry is black or hisp pref cand (map-specific)
#df 2: winners: rows = elec, columns = districts, entry is winner (note this df is for single map)
#df 3W1: rows = elec, columns = districts, entry is recency weight (not district or race specific)
#df 3W2: rows = elec, columns = districts, entries are min-preferred min weights (district specific, not race-specific)
#df 3BW3, 3HW3: rows = elec, columns = districts, entries are preferred-candidate-confidence weights (district and race specific)
    #(these are combined into one 3W3 df, by averaging the pairwise values in 3BW3 and 3HW3)

#df 1B and 1H: black_pref_cands_df, hisp_pref_cands_df
#make df with preferred candidate in every district for every election (only for enacted map)
        #also in this section- 2aii and 2bii dfs, or those whose entries are confidence in first choice pref. candidate pick
black_pref_cands_df = pd.DataFrame(columns = range(num_districts))
black_pref_cands_df["Election Set"] = elec_sets
hisp_pref_cands_df = pd.DataFrame(columns = range(num_districts))
hisp_pref_cands_df["Election Set"] = elec_sets

black_conf_W3 = pd.DataFrame(columns = range(num_districts))
black_conf_W3["Election Set"] = elec_sets
hisp_conf_W3 = pd.DataFrame(columns = range(num_districts))
hisp_conf_W3["Election Set"] = elec_sets    

start_time = time.time()
dist_Pbcvap = {}
dist_Phcvap = {}

#compute district winners
#df 3 winners (this example just for enacted map)
dist_winners = {} #adding district winners for each election to that election's df
map_winners = pd.DataFrame(columns = range(num_districts))

for j in elections:
    dist_winners[j] = winner(partition, j, candidates[j])
    keys = list(dist_winners[j].keys())
    values = list(dist_winners[j].values())
    map_winners.loc[len(map_winners)] = [value for _,value in sorted(zip(keys,values))]

map_winners = map_winners.reset_index(drop = True)
map_winners["Election"] = elections
map_winners["Election Set"] = elec_data_trunc["Election Set"]
map_winners["Election Type"] = elec_data_trunc["Type"]
map_winners.to_csv("winnersElec.csv")  
  
for district in range(num_districts): #get vector of precinct values for each district       
    dist_df = state_df[state_df[assign_test] == district]
    black_share = list(dist_df["BCVAP%"])
    hisp_share = list(dist_df["HCVAP%"])
    white_share = list(dist_df["WCVAP%"])    
        
    dist_Pbcvap[district] = sum(dist_df["BCVAP"])/sum(dist_df["CVAP"])
    dist_Phcvap[district] = sum(dist_df["HCVAP"])/sum(dist_df["CVAP"])
##########################################################################################################                                       
    #run ER regressions for black and hispanic voters        
    pop_weights = list(dist_df.loc[:,"CVAP"].apply(lambda x: x/sum(dist_df["CVAP"]))) 
    for elec in primary_elecs:             
        #remove points with cand-share-of-cvap >1 (cvap disagg error)
        cand_cvap_share_dict = {}
        black_share_dict = {}
        hisp_share_dict = {}
        pop_weights_dict = {}
        black_norm_params = {}
        hisp_norm_params = {}
        for cand in candidates[elec].values():
            cand_cvap_share = list(dist_df["{}%CVAP".format(cand)])
            cand_cvap_share_indices = [i for i,elem in enumerate(cand_cvap_share) if elem <= 1]
            
            cand_cvap_share_dict[cand] = list(itemgetter(*cand_cvap_share_indices)(cand_cvap_share))
            black_share_dict[cand] = list(itemgetter(*cand_cvap_share_indices)(black_share))
            hisp_share_dict[cand] = list(itemgetter(*cand_cvap_share_indices)(hisp_share))
            pop_weights_dict[cand] = list(itemgetter(*cand_cvap_share_indices)(pop_weights))
                                
        #now regrss cand share of total vote on demo-share-CVAP, black                                                                                     
            black_share_add = sm.add_constant(black_share_dict[cand])
            model = sm.WLS(cand_cvap_share_dict[cand], black_share_add, weights = pop_weights_dict[cand])            
            model = model.fit()
            cand_cvap_share_pred = model.predict()
            mean, std = norm_dist_params(cand_cvap_share_dict[cand], cand_cvap_share_pred, sum(model.params), pop_weights_dict[cand])
            black_norm_params[cand] = [mean,std]
            if district == display_dist and elec == display_elec:
                plt.figure(figsize=(12, 6))
                plt.scatter(black_share_dict[cand], cand_cvap_share_dict[cand], c = pop_weights_dict[cand], cmap = 'viridis_r')  
                    # scatter plot showing actual data
                plt.plot(black_share_dict[cand] +[1], list(cand_cvap_share_pred) + [sum(model.params)], 'r', linewidth=2) #extend lin regresssion line to 1
                plt.xticks(np.arange(0,1.1,.1))
                plt.yticks(np.arange(0,1.1,.1))
                plt.xlabel("BCVAP share of Precinct CVAP")
                plt.ylabel("{}'s share of precinct CVAP".format(cand))
                plt.title("ER, Black support for {}, district {}".format(cand, district+1))
                plt.savefig("Black {} support_{}.png".format(cand, district+1))
            
            #hisp share line fit/ ER               
            hisp_share_add = sm.add_constant(hisp_share_dict[cand])
            model = sm.WLS(cand_cvap_share_dict[cand], hisp_share_add, weights = pop_weights_dict[cand])            
            model = model.fit()
            cand_cvap_share_pred = model.predict()
            mean, std = norm_dist_params(cand_cvap_share_dict[cand], cand_cvap_share_pred, sum(model.params), pop_weights_dict[cand])
            hisp_norm_params[cand] = [mean,std]
            if district == display_dist and elec == display_elec:
                plt.figure(figsize=(12, 6))
                plt.scatter(hisp_share_dict[cand], cand_cvap_share_dict[cand], c = pop_weights_dict[cand], cmap = 'viridis_r')  
                    # scatter plot showing actual data
                plt.plot(hisp_share_dict[cand] +[1], list(cand_cvap_share_pred) + [sum(model.params)], 'r', linewidth=2) #extend lin regresssion line to 1
                plt.xticks(np.arange(0,1.1,.1))
                plt.yticks(np.arange(0,1.1,.1))
                plt.xlabel("HCVAP Share of Precinct CVAP")
                plt.ylabel("{}'s share of precinct CVAP".format(cand))
                plt.title("ER, Hisp support for {}, district {}".format(cand, district+1))
                plt.savefig("Hisp {} support.png".format(cand))
            
#####################################################################
        #optimizations for confidence! (W3)
        #populate black pref candidate and confidence in candidate (df 1a and 2aii)
        #if after dropping candidates under cand_drop_thresh, only one left, that is preferred candidate
        if len(black_norm_params) == 1:
            black_pref_cand = list(black_norm_params.keys())[0]
            black_pref_cands_df.at[black_pref_cands_df["Election Set"] == elec_match_dict[elec], district] = black_pref_cand
            black_conf_W3.at[black_conf_W3["Election Set"] == elec_match_dict[elec], district] = 1
        else:
            black_norm_params_copy = black_norm_params.copy()
            dist1_index = max(black_norm_params_copy.items(), key=operator.itemgetter(1))[0]
            dist1 = black_norm_params_copy[dist1_index]
            del black_norm_params_copy[dist1_index]
            dist2_index = max(black_norm_params_copy.items(), key=operator.itemgetter(1))[0]
            dist2 = black_norm_params_copy[dist2_index]
            
            if [0.0,0.0] in list(black_norm_params.values()):
                blank_index = [k for k,v in black_norm_params.items() if v == [0.0,0.0]][0]
                del black_norm_params[blank_index]
                
            res = scipy.optimize.minimize(lambda x, black_norm_params: -f(x, black_norm_params), (dist1[0]- dist2[0])/2+ dist2[0] , args=(black_norm_params), bounds = [(dist2[0], dist1[0])])       
            black_er_conf = abs(res.fun)[0]
            
            if district == display_dist and elec == display_elec:
                print("elec", elec)
                print("candidates", candidates[elec])
                print("params", black_norm_params)
                print("black first choice", dist1_index, dist1, dist1_index)
                print("first conf", black_er_conf)
                
                plt.figure(figsize=(12, 6))
                for j in black_norm_params.keys(): 
                   # if j != dist1_index and j != dist2_index:
                   #     continue
                    mean = black_norm_params[j][0]
                    std = black_norm_params[j][1]
                    x = np.linspace(mean - 3*std, mean + 3*std)
                    plt.plot(x,scipy.stats.norm.pdf(x,black_norm_params[j][0], black_norm_params[j][1]))
                    plt.axvline(x= mean, color = 'black')
                    dist_from_mean = abs(res.x[0]-mean)
                    iq=stats.norm(mean,std)
                   # section = np.arange(mean-1, mean+1, .01)
                    section = np.arange(mean-dist_from_mean, mean+dist_from_mean, .01)
                    plt.fill_between(section,iq.pdf(section)) 
                plt.title("Black dists")
                          
            #final black pref and confidence in choice
            black_pref_cand = dist1_index
            black_pref_cands_df.at[black_pref_cands_df["Election Set"] == elec_match_dict[elec], district] = black_pref_cand
            black_conf_W3.at[black_conf_W3["Election Set"] == elec_match_dict[elec], district] = black_er_conf
            
        #populate hisp pref candidate and confidence in candidate (df 1b and 2bii)
        if len(hisp_norm_params) == 1:
            hisp_pref_cand = list(hisp_norm_params.keys())[0]
            hisp_pref_cands_df.at[hisp_pref_cands_df["Election Set"] == elec_match_dict[elec], district] = hisp_pref_cand
            hisp_conf_W3.at[hisp_conf_W3["Election Set"] == elec_match_dict[elec], district] = 1
        else:
            hisp_norm_params_copy = hisp_norm_params.copy()
            dist1_index = max(hisp_norm_params_copy.items(), key=operator.itemgetter(1))[0]
            dist1 = hisp_norm_params_copy[dist1_index]
            del hisp_norm_params_copy[dist1_index]
            dist2_index = max(hisp_norm_params_copy.items(), key=operator.itemgetter(1))[0]
            dist2 = hisp_norm_params_copy[dist2_index]
            
            if [0.0,0.0] in list(hisp_norm_params.values()):
                blank_index = [k for k,v in hisp_norm_params.items() if v == [0.0,0.0]][0]
                del hisp_norm_params[blank_index]
                
            res = scipy.optimize.minimize(lambda x, hisp_norm_params: -f(x, hisp_norm_params), (dist1[0]- dist2[0])/2+ dist2[0] , args=(hisp_norm_params), bounds = [(dist2[0], dist1[0])])
            hisp_er_conf = abs(res.fun)[0]
            if district == display_dist and elec == display_elec:
                print("elec", elec)
                print("candidates", candidates[elec])
                print("params", hisp_norm_params)
                print("hisp first choice", dist1_index, dist1, dist1_index)
                print("first conf", hisp_er_conf)
                
                plt.figure(figsize=(12, 6))
                for j in hisp_norm_params.keys():  
                    if j != dist1_index and j != dist2_index:
                        continue
                    mean = hisp_norm_params[j][0]
                    std = hisp_norm_params[j][1]
                    x = np.linspace(mean - 3*std, mean + 3*std)
                    plt.plot(x,scipy.stats.norm.pdf(x,hisp_norm_params[j][0], hisp_norm_params[j][1]))
                    plt.axvline(x= mean, color = 'black')
                    dist_from_mean = abs(res.x[0]-mean)
                    iq=stats.norm(mean,std)
                   # section = np.arange(mean-1, mean+1, .01)
                    section = np.arange(mean-dist_from_mean, mean+dist_from_mean, .01)
                    plt.fill_between(section,iq.pdf(section)) 
                plt.title("hisp dists")
            #final hisp pref and confidence in choice
            hisp_pref_cand = dist1_index
            hisp_pref_cands_df.at[hisp_pref_cands_df["Election Set"] == elec_match_dict[elec], district] = hisp_pref_cand
            hisp_conf_W3.at[hisp_conf_W3["Election Set"] == elec_match_dict[elec], district] = hisp_er_conf
   
    end_time = time.time()      
#########################################################################################
#get election weights 1 and 2 and combine for final
black_weight_df = pd.DataFrame(columns = range(num_districts))
hisp_weight_df = pd.DataFrame(columns = range(num_districts))

#get weights W1 and W2 for weighting elections.   
recency_W1 = pd.DataFrame(columns = range(num_districts))
recency_W1["Election Set"] = elec_sets

min_cand_black_W2 = pd.DataFrame(columns = range(num_districts))
min_cand_black_W2["Election Set"] = elec_sets

min_cand_hisp_W2 = pd.DataFrame(columns = range(num_districts))
min_cand_hisp_W2["Election Set"] = elec_sets
        
for elec_set in elec_sets:
    elec_year = elec_data_trunc.loc[elec_data_trunc["Election Set"] == elec_set, 'Year'].values[0].astype(str)
    for dist in range(num_districts):      
        recency_W1.at[recency_W1["Election Set"] == elec_set, dist] = recency_weights[elec_year][0]

        black_pref = black_pref_cands_df.loc[black_pref_cands_df["Election Set"] == elec_set, dist].values[0]
        black_pref_race = cand_race_table.loc[cand_race_table["Candidates"] == black_pref, "Race"].values[0]
        black_pref_black = True if 'Black' in black_pref_race else False
        
        min_cand_weight_type = 'Relevant Minority' if black_pref_black else 'Other'
        min_cand_black_W2.at[min_cand_black_W2["Election Set"] == elec_set, dist] = min_cand_weights[min_cand_weight_type][0] 
        
        hisp_pref = hisp_pref_cands_df.loc[hisp_pref_cands_df["Election Set"] == elec_set, dist].values[0]
        hisp_pref_race = cand_race_table.loc[cand_race_table["Candidates"] == hisp_pref, "Race"].values[0]
        hisp_pref_hisp = True if 'Hispanic' in hisp_pref_race else False 
            
        min_cand_weight_type = 'Relevant Minority' if hisp_pref_hisp else 'Other'
        min_cand_hisp_W2.at[min_cand_hisp_W2["Election Set"] == elec_set, dist] = min_cand_weights[min_cand_weight_type][0] 
         
#min_cand_W2.to_csv("W2_df.csv")
#final 2a and 2b election probativity scores
black_weight_df = recency_W1.drop(["Election Set"], axis=1)*min_cand_black_W2.drop(["Election Set"], axis=1)*black_conf_W3.drop(["Election Set"], axis=1)
hisp_weight_df = recency_W1.drop(["Election Set"], axis=1)*min_cand_hisp_W2.drop(["Election Set"], axis=1)*hisp_conf_W3.drop(["Election Set"], axis=1)    

if elec_weighting == 'equal':
    for col in black_weight_df.columns:
        black_weight_df[col].values[:] = 1
    for col in hisp_weight_df.columns:
        hisp_weight_df[col].values[:] = 1
    
##############################################################################
#accrue points for black and hispanic voters if cand-of-choice wins
general_winners = map_winners[map_winners["Election Type"] == 'General'].reset_index(drop = True)
primary_winners = map_winners[map_winners["Election Type"] == 'Primary'].reset_index(drop = True)
runoff_winners = map_winners[map_winners["Election Type"] == 'Runoff'].reset_index(drop = True)

#determine if election set accrues points by district for black and Latino voters
black_pref_wins = pd.DataFrame(columns = range(num_districts))
black_pref_wins["Election Set"] = elec_sets
hisp_pref_wins = pd.DataFrame(columns = range(num_districts))
hisp_pref_wins["Election Set"] = elec_sets
for i in range(num_districts):
    for elec_set in elec_sets:
        black_pref_cand = black_pref_cands_df.loc[black_pref_cands_df["Election Set"] == elec_set, i].values[0]
        hisp_pref_cand = hisp_pref_cands_df.loc[hisp_pref_cands_df["Election Set"] == elec_set, i].values[0]       
        
        primary_winner = primary_winners.loc[primary_winners["Election Set"] == elec_set, i].values[0]
        general_winner = general_winners.loc[general_winners["Election Set"] == elec_set, i].values[0]
        runoff_winner = None if len(runoff_winners[runoff_winners["Election Set"] == elec_set]) == 0 \
        else runoff_winners.loc[runoff_winners["Election Set"] == elec_set, i].values[0]
        party_general_winner = cand_race_table.loc[cand_race_table["Candidates"] == general_winner, "Party"].values[0] 
                
        #winning conditions:
        print("black pref cand", black_pref_cand)
        print("primary winner", primary_winner)
        black_pref_wins.at[black_pref_wins["Election Set"] == elec_set, i] = True if \
        ((primary_winner == black_pref_cand) & (general_winner == black_pref_cand) \
        or (primary_winner == black_pref_cand) & (party_general_winner == 'D')) \
        else False
        
        hisp_pref_wins.at[hisp_pref_wins["Election Set"] == elec_set, i] = True if \
        ((primary_winner == hisp_pref_cand) & (general_winner == hisp_pref_cand) \
        or (primary_winner == hisp_pref_cand) & (party_general_winner == 'D'))\
        else False
        
black_points_accrued = (black_weight_df*black_pref_wins).drop(['Election Set'], axis =1)   
hisp_points_accrued = (hisp_weight_df*hisp_pref_wins).drop(['Election Set'], axis =1)   
    

###################################################################    
#Compute district probabilities
black_vra_prob = [0 if sum(black_weight_df[i]) == 0 else sum(black_points_accrued[i])/sum(black_weight_df[i]) for i in range(num_districts)]
hisp_vra_prob = [0 if sum(hisp_weight_df[i])  == 0 else sum(hisp_points_accrued[i])/sum(hisp_weight_df[i]) for i in range(num_districts)]   

################################################## 
#District by district probability breakdowns
dist_perc_df = pd.DataFrame(columns = ["District"])
dist_perc_df["District"] = list(range(1, num_districts+1))
dist_perc_df["Latino Perc"] = hisp_vra_prob
dist_perc_df["Black Perc"] = black_vra_prob
dist_perc_df.to_csv("Dist_perc df.csv")

#district deep dive
district_df = pd.DataFrame(columns = ["Election Set"])
district_entry = 1
district = district_entry-1
district_df["Election Set"] = elec_sets
#district_df["Winner"] = map_winners[district]
district_df["Primary Winner"] = primary_winners[district]
district_df["General Winner"] = general_winners[district]
district_df["Black cand"] = black_pref_cands_df[district]
district_df["Hisp cand"] = hisp_pref_cands_df[district]
district_df["Black pref wins"] = black_pref_wins[district]
district_df["Hisp pref wins"] = hisp_pref_wins[district]
  
district_df["Recency W1"] = recency_W1[district]
district_df["Min Pref Min Black W2"] = min_cand_black_W2[district] 
district_df["Min Pref Min Hisp W2"] = min_cand_hisp_W2[district] 
district_df["Black Conf W3"] = black_conf_W3[district]    
district_df["Hisp Conf W3"] = hisp_conf_W3[district]  

district_df["Black elec weight"] = black_weight_df[district] 
district_df["Hisp elec weight"] = hisp_weight_df[district]

district_df["Black points accrued"] = black_points_accrued[district]
district_df["Hisp points accrued"] = hisp_points_accrued[district]   

ratio_df = pd.DataFrame(columns = list(dist_perc_df.columns))
ratio_df.loc[0] =list(dist_perc_df.iloc[district]) 

writer = pd.ExcelWriter("District {} TX model, {}.xlsx".format(district+1, elec_weighting), engine = 'xlsxwriter')
district_df.to_excel(writer, sheet_name = "All")

#ratio_df.to_excel(writer, sheet_name = 'Ratios')
writer.save()


