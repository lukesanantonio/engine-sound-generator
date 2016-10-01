import noise
import pyaudio
import struct
import math
import time
import sys

# At the rate we are going, this is exactly one seconds worth of data.
SAMPLE_RATE = 44100

SAMPLE_SIZE = 4
FRAME_SIZE = SAMPLE_SIZE

# Frequency in hertz
START_FREQUENCY = 220

# Hz / second
ANIM_RATE = 250

class FreqConst:
    def __init__(self):
        pass
    def is_done(self):
        return False
    def next(self, dt, old_val):
        return old_val

class FreqAnimator:

    INIT_STATE = 'init'
    RUNNING_STATE = ''
    DONE_STATE = 'done'

    def __init__(self, rate, *states):
        self.states = list(states)
        # Amount of time in seconds to add a hertz.
        self.time_step = 1 / rate

        # We are only partially initialized, we only start when the user first
        # calls next.
        self.anim_state = FreqAnimator.INIT_STATE

    def reset(self):
        self.anim_state = FreqAnimator.INIT_STATE

    def is_done(self):
        return self.anim_state == FreqAnimator.DONE_STATE

    def next(self, dt, old_val):
        if self.anim_state == FreqAnimator.INIT_STATE:
            # This is our starting value!
            self.cur_value = old_val

            # Move towards the goal of state 0, starting without progress.
            self.state_i = 0
            self.state_progress = 0
            self.accum_time = 0.0

            # We are not done, and not partially initialized, which is sorta
            # what the init state signifies
            self.anim_state = FreqAnimator.RUNNING_STATE

        # As long as we are not done, do stuff, otherwise skip it all
        if self.anim_state != FreqAnimator.DONE_STATE:
            # Accumulate the time
            self.accum_time += dt

            # Time to do something
            if self.accum_time >= self.time_step:
                # We handled some time, so remove it for next time
                self.accum_time -= self.time_step

                # How much do we have to add total?
                goal = self.states[self.state_i]

                if goal == 0:
                    # Skip this state, and move on, since we've already technically
                    # satisfied it.
                    state += 1
                else:
                    # This will either be 1 or -1, it tells us how much to add to the
                    # old value.
                    direction = goal // abs(goal)

                    # How much have we moved?
                    self.state_progress += direction

                    # Do the move
                    self.cur_value += direction

                    # Haved we moved enough?
                    if abs(self.state_progress) >= abs(goal):
                        # Continue to the next state.
                        self.state_i += 1
                        # Reset our progress, but not our accumulated time.
                        self.state_progress = 0

                        if self.state_i >= len(self.states):
                            # We've gone through every state
                            self.anim_state = FreqAnimator.DONE_STATE

        # When we are done, this will just stay constant, so we don't have to
        # worry about it.
        return self.cur_value

def square(t, f):
    return 4 / math.pi * (math.sin(2 * math.pi *  f * t) +
                          1 / 3 * math.sin(6 * math.pi * f * t) +
                          1 / 5 * math.sin(10 * math.pi * f * t))

class InactiveGeneratorError(Exception):
    pass

class GeneratorAudio:

    STATE_OFF = 'off'
    STATE_STEADY = 'steady'
    STATE_GOING_UP = 'up'
    STATE_GOING_DOWN = 'down'

    def __init__(self, cur_freq = START_FREQUENCY, anim_rate = ANIM_RATE):
        self.last_time = 0.0
        self.buf = bytearray(0)
        self.state = GeneratorAudio.STATE_OFF
        self.cur_freq = cur_freq

        self.const_freq_anim = FreqConst()
        self.up_freq_anim = FreqAnimator(anim_rate, 100, -25, 50)
        self.down_freq_anim = FreqAnimator(anim_rate, -100, 25, -50)

        # We are going to need to reset these functions every time the state
        # changes!

    def step_up(self):
        if self.is_active():
            if self.state != GeneratorAudio.STATE_GOING_UP:
                # Reset animation.
                self.up_freq_anim.reset()

            self.state = GeneratorAudio.STATE_GOING_UP
        else:
            raise InactiveGeneratorError()

    def step_down(self):
        if self.is_active():
            if self.state != GeneratorAudio.STATE_GOING_DOWN:
                # Reset animation.
                self.down_freq_anim.reset()

            self.state = GeneratorAudio.STATE_GOING_DOWN
        else:
            raise InactiveGeneratorError()

    def is_active(self):
        return self.state != GeneratorAudio.STATE_OFF

    def start(self):
        if not self.is_active():
            self.state = GeneratorAudio.STATE_STEADY
    def stop(self):
        if self.is_active():
            self.state = GeneratorAudio.STATE_OFF

    def pa_callback(self, in_data, frame_count, time_info, status):
        if self.is_active():
            # Return noises
            return self.aud_cb(frame_count), pyaudio.paContinue
        else:
            # Return silence
            return bytes(frame_count * FRAME_SIZE), pyaudio.paContinue

    def aud_cb(self, frame_count):
        # Resize the buffer if necessary.
        if frame_count * FRAME_SIZE != len(self.buf):
            self.buf = bytearray(frame_count * FRAME_SIZE)

        freq = self.cur_freq

        freq_anim = self.const_freq_anim
        if self.state == GeneratorAudio.STATE_GOING_UP:
            # We are going up, set up that 'animator'.
            freq_anim = self.up_freq_anim
        elif self.state == GeneratorAudio.STATE_GOING_DOWN:
            # We are going down, etc.
            freq_anim = self.down_freq_anim

        # Generate some data
        for i in range(frame_count):
            dt = i / SAMPLE_RATE
            cur_time = self.last_time + dt

            val = square(cur_time, freq) * .02 + \
                  noise.pnoise1(cur_time * freq, octaves=5,
                                persistence=.95,lacunarity=2.0) * .98
            val *= 1.1
            struct.pack_into('<f', self.buf, FRAME_SIZE * i, val)

            freq = freq_anim.next(dt, freq)

            # If it's done, it's time to go back to constant
            if freq_anim.is_done():
                self.state = GeneratorAudio.STATE_STEADY
                freq_anim = self.const_freq_anim

        self.last_time = cur_time
        self.cur_freq = freq

        # Return data.
        return bytes(self.buf)

if __name__ == '__main__':
    p = pyaudio.PyAudio()

    eng = GeneratorAudio()

    stream = p.open(format=pyaudio.paFloat32, channels=1,
                    rate=SAMPLE_RATE, output=True,
                    stream_callback=eng.pa_callback)
    stream.start_stream()

    while stream.is_active():
        try:
            # How nice, a command prompt!
            cmd = input('> ')
        except KeyboardInterrupt:
            # Clear the line so the user's prompt doesn't show up on the same
            # line.
            sys.stdout.write('\n')
            sys.stdout.flush()
            cmd = "quit"

        # Step up the "engine"
        if cmd == 'start':
            print('Starting engine...')
            eng.start()
        elif cmd == 'stop':
            print('Stopping engine...')
            eng.stop()
        elif cmd == 'step':
            print('Stepping up...')
            eng.step_up()
        # Step down the "engine"
        elif cmd == 'down':
            print('Stepping down...')
            eng.step_down()
        # Print engine information
        elif cmd == 'status':
            # This happens every iteration, just be quiet about it.
            pass
        # Quit the program
        elif cmd == "quit":
            print("Quitting...")
            break
        # Read the source code!
        elif cmd == 'help':
            print('No help for you!')
        else:
            print('Unknown command, try again!')

        print('Engine state:', eng.state, 'Freq:', eng.cur_freq)

    # Clean up
    stream.stop_stream()
    stream.close()

    p.terminate()
