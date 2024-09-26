import vpype
from pathlib import Path
import argparse
import socket
import threading
from queue import Queue
import importlib.resources

CMD_MOVEL = 1


coord_queue = Queue()


def send_script_to_robot(robot_ip, script):
    """Send the URScript to the robot."""
    robot_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    robot_socket.settimeout(5)
    try:
        robot_socket.connect((robot_ip, 30002))
        # URScript must end with a newline character
        robot_socket.sendall((script + '\r\n').encode('utf-8'))
        robot_socket.close()
        print('Script sent to robot.')
    except socket.error as e:
        print('Failed to send script to robot:', e)
        exit(1)


def robot_communication_thread(server_ip, server_port, robot_ip, coord_queue):
    """Handle communication with the robot."""

    # URScript with placeholders for server IP and port
    script_res = importlib.resources.files('ur_net_plot').joinpath('urscript/plot.urscript')

    with importlib.resources.as_file(script_res) as script_file_path:
        with open(script_file_path, 'r') as script_file:
            plot_script_template = script_file.read()

    # Fill in the placeholders with actual server IP and port
    script = plot_script_template.format(SERVER_IP=server_ip, SERVER_PORT=server_port)

    # Set up server socket to accept connection from the robot
    print(f'Binding to {server_ip}:{server_port}')
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((server_ip, server_port))
    server_socket.listen(1)
    server_socket.settimeout(10)  # 10 seconds timeout
    print('Waiting for robot to connect...')

    # Send the script to the robot
    send_script_to_robot(robot_ip, script)

    try:
        robot_conn, addr = server_socket.accept()
        print('Robot connected from', addr)
    except socket.timeout:
        print('Robot did not connect within timeout.')
        exit(1)

    while True:
        # Get coordinates from queue
        coords = coord_queue.get()
        if coords is None:
            break  # Exit signal

        x, y, z = coords

        # Convert meters to tenths of mm
        x_int = int(x * 10000)
        y_int = int(y * 10000)
        z_int = int(z * 10000)

        command = [CMD_MOVEL, x_int, y_int, z_int, 0, 0, 0]

        # Pack the integers into binary format (big-endian, signed integers)
        data = b''.join(int.to_bytes(val, 4, byteorder='big', signed=True) for val in command)

        try:
            # Send the command
            robot_conn.sendall(data)

            # Wait for robot to send back the zero value
            response = robot_conn.recv(4)

            if len(response) < 4:
                print('Robot connection closed unexpectedly.')
                break

            # Unpack the response
            result = int.from_bytes(response, byteorder='big', signed=True)

            if result != 0:
                print('Robot reported error:', result)
        except socket.error as e:
            print('Communication error:', e)
            break

    robot_conn.close()
    server_socket.close()


def draw_square(draw_height):
    # Enqueue the coordinates to draw the 100mm square
    # Start 50mm above the surface
    coord_queue.put((0.0, 0.0, -0.05))
    # Lower to the surface
    coord_queue.put((0.0, 0.0, draw_height))
    # Draw the square
    coord_queue.put((0.1, 0.0, draw_height))
    coord_queue.put((0.1, 0.1, draw_height))
    coord_queue.put((0.0, 0.1, draw_height))
    coord_queue.put((0.0, 0.0, draw_height))
    # Raise back to 50mm above the surface
    coord_queue.put((0.0, 0.0, -0.05))


def draw_svg(svg_path, target_size, draw_height, lift_height=-0.05):
    lines, width, height = vpype.read_svg(svg_path, quantization=2.5)
    lines = vpype.squiggles(lines, 0, 1, 10)

    # Rescale the SVG to fit the target size
    target_width = target_size
    target_height = target_size * height / width
    lines.scale(target_width / width, target_height / height)

    first_x, first_y = lines[0][0].real, lines[0][0].imag
    coord_queue.put((first_x, first_y, lift_height))

    for path in lines:
        for pt in path:
            x, y = pt.real, pt.imag
            coord_queue.put((x, y, draw_height))

        coord_queue.put((x, y, lift_height))


def main():
    parser = argparse.ArgumentParser(description='Robot Server')
    parser.add_argument('--robot_ip', type=str, required=True, help='IP address of the robot')
    parser.add_argument('--server_ip', type=str, default='0.0.0.0', help='IP address to bind the server')
    parser.add_argument('--server_port', type=int, default=30000, help='Port to bind the server')
    parser.add_argument('svg', type=str, help='SVG file to plot')
    args = parser.parse_args()

    svg_path = Path(args.svg)
    if not svg_path.exists():
        print('File not found:', args.svg)
        exit(1)

    # Start the robot communication thread
    robot_thread = threading.Thread(target=robot_communication_thread, args=(args.server_ip, args.server_port, args.robot_ip, coord_queue))
    robot_thread.start()

    draw_svg(svg_path, 0.3, 0.003)

    # Signal the thread to exit after all commands are sent
    coord_queue.put(None)
    robot_thread.join()
    print('Drawing complete.')


if __name__ == '__main__':
    main()
