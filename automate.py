# import stuff

# generate instructions: axis, step size, number of steps (each way?)

# loop n times:
#   take data
#   make incremental move
# then return and loop the other way (-incremental move)

# make plot


import time, datetime
import argparse
import numpy as np
import matplotlib.pyplot as plt
import threading

from scipy.signal import argrelextrema


import nidaqmx
import winsound

import requests
from discord import Webhook, RequestsWebhookAdapter

from hexachamber import HexaChamber
from positioner import Positioner
import na_tracer

class AutoScanner():

    def __init__(self, hexachamber, positioner, na_tracer):
        self.hex = hexachamber
        self.pos = positioner
        self.na = na_tracer 
        self.hexstatus ='init'



    def safety_check(self, danger_volts=0.1, channel='ai0', task_number=1, timeout=30):
        '''
        Continually measures the potential difference between the cavity
            and the plate. If the potential difference drops below the critical
            voltage, that implies they are touching, and we should stop moving
            the hexapod or move it back to the original position

        Input:
        TODO finish commenting
        TODO have some way to manually stop the readings

        Returns:
            touching - boolean
        
        '''
        
        frequency = 2500  # Set Frequency To 2500 Hertz
        duration = 1000  # Set Duration To 1000 ms == 1 second
        taskname = 'Dev' + str(task_number)
        voltage_channel = '/'.join([taskname, channel])
        touching = False
        with nidaqmx.Task() as task:
            task.ai_channels.add_ai_voltage_chan(voltage_channel)
            if(timeout is None):
                timeout = -1
            time_start = time.time()
            time_elapsed = time.time() - time_start
            curr_voltage = task.read()
            print('Current voltage: ' + str(curr_voltage))
            while((time_elapsed < timeout or timeout < 0) and self.hexstatus == 'scanning'):
                voltage_btw_plate_cavity = task.read()
                if(voltage_btw_plate_cavity < danger_volts):
                    touching = True
                    print("Plate and cavity are touching! (or the power supply is off...)")
                    self.hexstatus = 'stop'
                    err, msg = self.hex.abort_all()
                    print(err)
                    print(msg)
                    winsound.Beep(frequency, duration)
                    break 
                time_elapsed = time.time()-time_start
        print('End safety')
        return touching
    
    


    def tuning_scan_safety(self, tuning_sequence, delay=0.5, safe_check=False, DATA_TYPE=''):
        '''
        hex: HexaChamber object
        tuning_sequence: list of dicts of step sizes (dX:val,dY:val,dZ,dU,dV,dW), you get it
        '''
        danger_volts = 0.1
        channel = 'ai0'
        taskno = 1
        timeout = None
        safety_thread = threading.Thread(target=self.safety_check, 
                                    args=[danger_volts, channel, taskno, timeout])
        print('Starting scan...')
        self.hexstatus = 'scanning'
        if(safe_check):
            safety_thread.start()
        responses = None

        freqs = self.na.get_pna_freq()
        for i,step in enumerate(tuning_sequence):
            
            print(f'Performing move: {step} ({i+1} of {len(tuning_sequence)})')

            if len(step.keys()) != 1:
                print('only implemented moving one parameter at once so far!')
                exit(-1)

            param_name = list(step.keys())[0]

            if param_name == 'dU' or param_name == 'dV' or param_name == 'dW':
                coord_sys = 'Tool'
            if param_name == 'dX' or param_name == 'dY' or param_name == 'dZ':
                coord_sys = 'Work'
            
            self.hex.incremental_move(**step, coord_sys=coord_sys)

            if(self.hexstatus == 'stop'):
                break
            time.sleep(delay)
            if(self.hexstatus == 'stop'):
                break
            
            if i == len(tuning_sequence)-1:
                # don't take data after re-centering move
                continue

            total_retries = 10
            for attempt in range(total_retries):

                response = self.na.get_pna_response()
                if(response is None):
                    print(f'VNA asleep!, trying again (attempt {attempt+1}/{total_retries})')
                    continue
                else:
                    break

            if i == 0:
                responses = np.zeros((len(tuning_sequence)-1, len(response)))
            responses[i] = response
            if(self.hexstatus == 'stop'):
                break
        self.hexstatus = 'stop'
        return responses, freqs

def generate_single_axis_seq(coord, incr, start, end):
    '''
    Generates the list of coordinates to move the hexapod 
    TODO: comment more
    '''
    num_moves = int((end-start)/incr)
    seq = [{coord:incr} for i in range(num_moves)]
    seq.insert(0, {coord:start})
    seq.append({coord:-end})
    return seq
 
def tuning_scan(hex, na, tuning_sequence, delay=15):
    '''
    DON'T USE THIS, USE tuning_scan_safety
    hex: HexaChamber object
    tuning_sequence: list of dicts of step sizes (dX:val,dY:val,dZ,dU,dV,dW), you get it
    '''

    print('Starting scan...')
    for i,step in enumerate(tuning_sequence):
        time.sleep(delay)
        response = na.get_pna_response()
        if i == 0:
            responses = np.zeros((len(tuning_sequence), len(response)))
        responses[i] = response
        hex.incremental_move(**step)

    return responses    

def plot_tuning(responses,freqs, start_pos, coord, start, end):

    coords = np.array(['dX', 'dY', 'dZ', 'dU', 'dV', 'dW'])
    init_param = start_pos[np.where(coords==coord)][0]

    freqs = freqs/10**9 # GHz
    plt.imshow(responses, extent=[freqs[0], freqs[-1], end+init_param, start+init_param], interpolation='none', aspect='auto', cmap='plasma_r')
    plt.xlabel('Frequency [GHz]')
    plt.ylabel(f'Tuning Parameter: {coord[-1]}')
    plt.colorbar()

def save_tuning(responses, freqs, start_pos, coord, start, end):
    data_dir = "C:\\Users\\FTS\\source\\repos\\axion_detector\\tuning_data\\"
    now_str = datetime.datetime.today().strftime('%Y-%m-%d-%H-%M-%S')
    fname = f"{now_str}_{start_pos[0]}X{start_pos[1]}Y{start_pos[2]}Z{start_pos[3]}U{start_pos[4]}V{start_pos[5]}W{start}i{end}f{coord}"
    print(f"Saving to: {data_dir}\\{fname}.npy")
    np.save(f"{data_dir}{fname}", np.vstack((freqs,responses)))

def scan_one(coord, start, end, incr, plot=True, save=True):
    
    err,start_pos = hex.get_position()
    if err != 0:
        print(f'ERROR {err} with hexapod, exiting')
        hex.close()
        exit(err)
    seq = generate_single_axis_seq(coord=coord, incr=incr, start=start, end=end)
    responses, freqs = auto.tuning_scan_safety(seq, delay=0.2)
    if plot:
        plt.figure(figsize=[8,6])
        plot_tuning(responses, freqs, start_pos, coord, start, end)
    if save:
        save_tuning(responses, freqs, start_pos, coord, start, end)
    return responses


def scan_many(coords, starts, ends, incrs, plot=True, save=True):

    err,start_pos = hex.get_position()
    if err != 0:
        print(f'ERROR {err} with hexapod, exiting')
        hex.close()
        exit(err)

    mode_maps = None # (coord numbr, responses)
    for i in range(len(coords)):
        seq = generate_single_axis_seq(coord=coords[i], incr=incrs[i], start=starts[i], end=ends[i])
        responses, freqs = auto.tuning_scan_safety(seq, delay=0.2)
        if plot:
            plt.figure(figsize=[12,10])
            plot_tuning(responses, freqs, start_pos, coords[i], starts[i], ends[i])
        if save:
            save_tuning(responses, freqs, start_pos, coords[i], starts[i], ends[i])
        if i == 0:
            mode_maps = np.zeros((len(coords),*responses.shape))
        mode_maps[i] = responses

    return mode_maps

def scan_multialignment(hex, auto, coords, starts, ends, incrs, plot=True, save_plots=True, save_data=True,):
    '''
    Take several scans along coords[0], perturbing coords[1] after each scan
    
    ONLY WORKS FOR TWO PARAMS AT A TIME
    '''

    N_cycles = np.arange(starts[1],ends[1]+incrs[1],incrs[1]).size

    # set start of coords[1]
    kwarg = {coords[1]: starts[1]}
    hex.incremental_move(**kwarg)

    for frame in range(N_cycles):

        err,start_pos = hex.get_position()

        print(f"hexapod started cycle {frame}/{(ends[1]-starts[1])/incrs[1]} at {start_pos}")

        if err != 0:
            print(f'ERROR {err} with hexapod, exiting')
            hex.close()
            exit(err)
        
        seq = generate_single_axis_seq(coord=coords[0], incr=incrs[0], start=starts[0], end=ends[0])
        responses, freqs = auto.tuning_scan_safety(seq, delay=0.2)
        if(responses is not None):
            if plot:
                plt.figure(figsize=[12,10])
                plot_tuning(responses, freqs, start_pos, coords[0], starts[0], ends[0])
                if save_plots:
                    plt.savefig(f"plots/dV_{start_pos[4]}X.png")
            if save_data:
                save_tuning(responses, freqs, start_pos, coords[0], starts[0], ends[0])

        kwarg = {coords[1]: incrs[1]}
        hex.incremental_move(**kwarg)

    kwarg = {coords[1]: -N_cycles*incrs[1]-starts[1]}
    hex.incremental_move(**kwarg)
    
def autoalign(coords, margins, max_iters=10):
    '''
    Align automatically.

    takes a list of parameters, and the error margin to align to, and a max_iters
    '''

    # While not out of iters,
        # For each param, from most to least impactful:
            # make a scan of a few (~20?) points on param axis
            # fit maximum frequency point of fundamental (quadratic? Hyperbola?)
            # move to that minimum
        # if all params in margin, break

    start = -0.1
    end = -start
    incr = end/20

    iter = 0
    while iter < max_iters:
        for coord in coords:
            responses = scan_one(coord, start, end, incr, plot=True, save=False)
            

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--hex_ip', default='192.168.254.254',
                    help='IP address to connect to the NewportXPS hexapod')
    parser.add_argument('-j', '--pos_ip', default='192.168.0.254',
                    help='IP address to connect to the NewportXPS positioner')
    parser.add_argument('-p', '--hex_password', help='Password to connect to the NewportXPS hexapod')
    parser.add_argument('-q', '--pos_password', help='Password to connect to the NewportXPS positioner' )
    parser.add_argument('-r', '--reinitialize', action='store_true', 
                        help='Whether to reinitialize the xps machines')
    args = parser.parse_args()
    
    print('****************************')
    password = args.pos_password
    IP = args.pos_ip

    hex = HexaChamber(host=args.hex_ip, username='Administrator', password=args.hex_password)
    #pos = Positioner(host=args.pos_ip, username='Administrator', password=args.pos_password)
    na = na_tracer.NetworkAnalyzer()

    webhook = Webhook.from_url("https://discordapp.com/api/webhooks/903012918126346270/wKyx27DEes1nibOCvu1tM6T5F4zkv60TNq-J0UkFDY-9WyZ2izDCZ_-VbpHvceeWsFqF", adapter=RequestsWebhookAdapter())

    auto = AutoScanner(hex, None, na)
    '''
    coords = np.array(['dX', 'dY', 'dU', 'dV', 'dW'])
    starts = np.array([-0.1, -0.2, -0.6, -0.05, -0.05])
    ends = -1*starts
    incrs = 0.05*ends
    '''
    coords = ['dV', 'dX']
    starts = np.array([-0.3, -0.06])
    ends = -1*starts
    incrs = 0.01*ends

    #scan_multialignment(hex, auto, coords, starts, ends, incrs)

    webhook.send(f"Scan of {coords} COMPLETE")

    plt.show()

    hex.close()

if __name__ == '__main__':
    main()